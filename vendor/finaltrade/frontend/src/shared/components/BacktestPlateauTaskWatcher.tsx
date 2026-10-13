import { useEffect, useMemo, useRef } from 'react'
import { App as AntdApp } from 'antd'
import { ApiError } from '@/shared/api/client'
import { isApiNotFoundError } from '@/shared/api/errors'
import { getBacktestPlateauTask, listBacktestPlateauTasks } from '@/shared/api/endpoints'
import { useBacktestPlateauTaskStore } from '@/state/backtestPlateauTaskStore'
import type { BacktestPlateauTaskStatusResponse } from '@/types/contracts'

const TASK_POLL_INTERVAL_MS = 1200
const TASK_TRANSIENT_WARNING_INTERVAL_MS = 15000

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message || `request failed: ${error.code}`
  if (error instanceof Error) return error.message || 'request failed'
  return 'request failed'
}

export function BacktestPlateauTaskWatcher() {
  const { message } = AntdApp.useApp()
  const activeTaskIds = useBacktestPlateauTaskStore((state) => state.activeTaskIds)
  const upsertTaskStatus = useBacktestPlateauTaskStore((state) => state.upsertTaskStatus)
  const removeTask = useBacktestPlateauTaskStore((state) => state.removeTask)

  const activeTaskKey = useMemo(() => activeTaskIds.slice().sort().join('|'), [activeTaskIds])
  const notifiedTaskStateRef = useRef<Record<string, string>>({})
  const warningThrottleRef = useRef<Record<string, number>>({})

  useEffect(() => {
    if (activeTaskIds.length <= 0) return

    let timer: ReturnType<typeof setTimeout> | null = null
    let active = true

    const notifyTaskStatus = (taskId: string, status: BacktestPlateauTaskStatusResponse) => {
      const statusText = `${status.status}:${status.progress.updated_at}`
      if (notifiedTaskStateRef.current[taskId] === statusText) return
      if (status.status === 'succeeded') {
        notifiedTaskStateRef.current[taskId] = statusText
        message.success(`收益平原任务已完成：${taskId}`)
        return
      }
      if (status.status === 'cancelled') {
        notifiedTaskStateRef.current[taskId] = statusText
        message.info(`收益平原任务已停止：${taskId}`)
        return
      }
      if (status.status === 'failed') {
        notifiedTaskStateRef.current[taskId] = statusText
        message.error(status.error?.trim() || `收益平原任务失败：${taskId}`)
      }
    }

    const markTaskMissing = (taskId: string) => {
      const missingKey = 'missing:pruned'
      if (notifiedTaskStateRef.current[taskId] === missingKey) return
      notifiedTaskStateRef.current[taskId] = missingKey
      removeTask(taskId)
    }

    const poll = async () => {
      const currentTaskIds = [...activeTaskIds]
      if (currentTaskIds.length <= 0 || !active) return

      let pollingTaskIds = currentTaskIds
      try {
        const listed = await listBacktestPlateauTasks()
        const remoteById = new Map(listed.items.map((item) => [item.task_id, item]))
        const normalizedPollingTaskIds: string[] = []
        for (const taskId of currentTaskIds) {
          const remote = remoteById.get(taskId)
          if (!remote) {
            markTaskMissing(taskId)
            continue
          }
          upsertTaskStatus(remote)
          notifyTaskStatus(taskId, remote)
          normalizedPollingTaskIds.push(taskId)
        }
        pollingTaskIds = normalizedPollingTaskIds
      } catch {
        // fallback: if list API fails, continue direct polling
      }

      if (!active) return
      if (pollingTaskIds.length <= 0) {
        timer = setTimeout(poll, TASK_POLL_INTERVAL_MS)
        return
      }

      const results = await Promise.all(
        pollingTaskIds.map(async (taskId) => {
          try {
            const status = await getBacktestPlateauTask(taskId)
            return { taskId, status, error: undefined as unknown }
          } catch (error) {
            return { taskId, status: undefined as BacktestPlateauTaskStatusResponse | undefined, error }
          }
        }),
      )

      if (!active) return

      for (const row of results) {
        if (row.status) {
          upsertTaskStatus(row.status)
          notifyTaskStatus(row.taskId, row.status)
          continue
        }

        if (isApiNotFoundError(row.error)) {
          markTaskMissing(row.taskId)
          continue
        }

        const now = Date.now()
        const lastWarningAt = warningThrottleRef.current[row.taskId] ?? 0
        if (now - lastWarningAt >= TASK_TRANSIENT_WARNING_INTERVAL_MS) {
          warningThrottleRef.current[row.taskId] = now
          message.warning(`收益平原任务轮询异常（稍后重试）：${getErrorMessage(row.error)}`)
        }
      }

      if (!active) return
      timer = setTimeout(poll, TASK_POLL_INTERVAL_MS)
    }

    void poll()

    return () => {
      active = false
      if (timer) clearTimeout(timer)
    }
  }, [activeTaskKey, activeTaskIds, message, removeTask, upsertTaskStatus])

  return null
}

