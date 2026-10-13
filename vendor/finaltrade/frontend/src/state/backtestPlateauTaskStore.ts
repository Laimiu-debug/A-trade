import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { BacktestPlateauTaskStatusResponse } from '@/types/contracts'

const BACKTEST_PLATEAU_TASKS_STORE_KEY = 'tdx-trend-backtest-plateau-tasks-v1'
const BACKTEST_PLATEAU_TASKS_MAX_COUNT = 120

interface BacktestPlateauTaskStoreState {
  tasksById: Record<string, BacktestPlateauTaskStatusResponse>
  activeTaskIds: string[]
  selectedTaskId?: string
  enqueueTask: (taskId: string, samplingMode: 'grid' | 'lhs') => void
  upsertTaskStatus: (status: BacktestPlateauTaskStatusResponse) => void
  markTaskFailed: (taskId: string, error: string, errorCode?: string) => void
  setSelectedTask: (taskId?: string) => void
  removeTask: (taskId: string) => void
  clearFinishedTasks: () => void
}

function nowIso() {
  return new Date().toISOString()
}

function buildPendingTask(taskId: string, samplingMode: 'grid' | 'lhs'): BacktestPlateauTaskStatusResponse {
  const timestamp = nowIso()
  return {
    task_id: taskId,
    status: 'pending',
    progress: {
      sampling_mode: samplingMode,
      processed_points: 0,
      total_points: 0,
      percent: 0,
      message: '收益平原任务已提交，等待后台执行。',
      started_at: timestamp,
      updated_at: timestamp,
    },
    result: null,
    error: null,
    error_code: null,
  }
}

function sortTasksByUpdatedAtDesc(tasksById: Record<string, BacktestPlateauTaskStatusResponse>) {
  return Object.values(tasksById).sort((left, right) => {
    const leftTs = Date.parse(left.progress.updated_at || left.progress.started_at || '')
    const rightTs = Date.parse(right.progress.updated_at || right.progress.started_at || '')
    return rightTs - leftTs
  })
}

function trimTaskMap(tasksById: Record<string, BacktestPlateauTaskStatusResponse>) {
  const sorted = sortTasksByUpdatedAtDesc(tasksById)
  const kept = sorted.slice(0, BACKTEST_PLATEAU_TASKS_MAX_COUNT)
  const next: Record<string, BacktestPlateauTaskStatusResponse> = {}
  for (const item of kept) {
    next[item.task_id] = item
  }
  return next
}

export const useBacktestPlateauTaskStore = create<BacktestPlateauTaskStoreState>()(
  persist(
    (set) => ({
      tasksById: {},
      activeTaskIds: [],
      selectedTaskId: undefined,
      enqueueTask: (taskId, samplingMode) =>
        set((state) => {
          const existing = state.tasksById[taskId]
          const merged = {
            ...state.tasksById,
            [taskId]: existing ?? buildPendingTask(taskId, samplingMode),
          }
          const trimmed = trimTaskMap(merged)
          const activeTaskIds = [taskId, ...state.activeTaskIds.filter((id) => id !== taskId)].filter(
            (id) => Boolean(trimmed[id]),
          )
          return {
            tasksById: trimmed,
            activeTaskIds,
            selectedTaskId: taskId,
          }
        }),
      upsertTaskStatus: (status) =>
        set((state) => {
          const merged = {
            ...state.tasksById,
            [status.task_id]: status,
          }
          const trimmed = trimTaskMap(merged)
          const taskActive = status.status === 'pending' || status.status === 'running'
          const baseActive = state.activeTaskIds.filter((id) => id !== status.task_id)
          const activeTaskIds = (taskActive ? [status.task_id, ...baseActive] : baseActive).filter((id) => Boolean(trimmed[id]))
          const selectedTaskId =
            state.selectedTaskId && trimmed[state.selectedTaskId]
              ? state.selectedTaskId
              : status.task_id
          return {
            tasksById: trimmed,
            activeTaskIds,
            selectedTaskId,
          }
        }),
      markTaskFailed: (taskId, error, errorCode) =>
        set((state) => {
          const current = state.tasksById[taskId]
          const timestamp = nowIso()
          const nextStatus: BacktestPlateauTaskStatusResponse = current
            ? {
                ...current,
                status: 'failed',
                error,
                error_code: errorCode ?? current.error_code ?? 'BACKTEST_PLATEAU_TASK_FAILED',
                progress: {
                  ...current.progress,
                  message: '收益平原任务失败。',
                  updated_at: timestamp,
                },
              }
            : {
                ...buildPendingTask(taskId, 'lhs'),
                status: 'failed',
                error,
                error_code: errorCode ?? 'BACKTEST_PLATEAU_TASK_FAILED',
                progress: {
                  ...buildPendingTask(taskId, 'lhs').progress,
                  message: '收益平原任务失败。',
                  updated_at: timestamp,
                },
              }
          const merged = {
            ...state.tasksById,
            [taskId]: nextStatus,
          }
          const trimmed = trimTaskMap(merged)
          const activeTaskIds = state.activeTaskIds.filter((id) => id !== taskId && Boolean(trimmed[id]))
          const selectedTaskId =
            state.selectedTaskId && trimmed[state.selectedTaskId]
              ? state.selectedTaskId
              : taskId
          return {
            tasksById: trimmed,
            activeTaskIds,
            selectedTaskId,
          }
        }),
      setSelectedTask: (taskId) =>
        set((state) => {
          if (!taskId) {
            const sorted = sortTasksByUpdatedAtDesc(state.tasksById)
            return {
              selectedTaskId: sorted[0]?.task_id,
            }
          }
          if (!state.tasksById[taskId]) {
            return state
          }
          return {
            selectedTaskId: taskId,
          }
        }),
      removeTask: (taskId) =>
        set((state) => {
          if (!state.tasksById[taskId]) return state
          const { [taskId]: _, ...rest } = state.tasksById
          const sorted = sortTasksByUpdatedAtDesc(rest)
          const nextSelected =
            state.selectedTaskId === taskId ? sorted[0]?.task_id : state.selectedTaskId
          return {
            tasksById: rest,
            activeTaskIds: state.activeTaskIds.filter((id) => id !== taskId),
            selectedTaskId: nextSelected,
          }
        }),
      clearFinishedTasks: () =>
        set((state) => {
          const keptEntries = Object.entries(state.tasksById).filter(
            ([, task]) => task.status === 'pending' || task.status === 'running' || task.status === 'paused',
          )
          const tasksById = Object.fromEntries(keptEntries)
          const sorted = sortTasksByUpdatedAtDesc(tasksById)
          return {
            tasksById,
            activeTaskIds: sorted.map((task) => task.task_id),
            selectedTaskId: sorted[0]?.task_id,
          }
        }),
    }),
    {
      name: BACKTEST_PLATEAU_TASKS_STORE_KEY,
      partialize: (state) => {
        const tasksById: Record<string, BacktestPlateauTaskStatusResponse> = {}
        for (const [taskId, task] of Object.entries(state.tasksById)) {
          tasksById[taskId] = {
            ...task,
            result: null,
          }
        }
        return {
          tasksById,
          activeTaskIds: state.activeTaskIds,
          selectedTaskId: state.selectedTaskId,
        }
      },
    },
  ),
)
