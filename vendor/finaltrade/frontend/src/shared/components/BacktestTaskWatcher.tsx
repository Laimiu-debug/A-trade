import { useEffect, useMemo, useRef } from 'react'
import { App as AntdApp } from 'antd'
import { ApiError } from '@/shared/api/client'
import { getBacktestTask } from '@/shared/api/endpoints'
import { useBacktestTaskStore } from '@/state/backtestTaskStore'

const TASK_POLL_INTERVAL_MS = 1200
const TASK_TRANSIENT_WARNING_INTERVAL_MS = 15000

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message || `请求失败: ${error.code}`
  if (error instanceof Error) return error.message || '请求失败'
  return '请求失败'
}

export function BacktestTaskWatcher() {
  const { message } = AntdApp.useApp()
  const activeTaskIds = useBacktestTaskStore((state) => state.activeTaskIds)
  const upsertTaskStatus = useBacktestTaskStore((state) => state.upsertTaskStatus)
  const markTaskFailed = useBacktestTaskStore((state) => state.markTaskFailed)

  const activeTaskKey = useMemo(() => activeTaskIds.slice().sort().join('|'), [activeTaskIds])
  const notifiedTaskStateRef = useRef<Record<string, string>>({})
  const warningThrottleRef = useRef<Record<string, number>>({})

  useEffect(() => {
    if (activeTaskIds.length <= 0) return

    let timer: ReturnType<typeof setTimeout> | null = null
    let active = true

    const poll = async () => {
      const currentTaskIds = [...activeTaskIds]
      if (currentTaskIds.length <= 0 || !active) return

      const results = await Promise.all(
        currentTaskIds.map(async (taskId) => {
          try {
            const status = await getBacktestTask(taskId)
            return { taskId, status, error: undefined }
          } catch (error) {
            return { taskId, status: undefined, error }
          }
        }),
      )

      if (!active) return

      for (const row of results) {
        if (row.status) {
          upsertTaskStatus(row.status)
          const statusText = `${row.status.status}:${row.status.progress.updated_at}`
          if (notifiedTaskStateRef.current[row.taskId] === statusText) {
            continue
          }
          if (row.status.status === 'succeeded') {
            notifiedTaskStateRef.current[row.taskId] = statusText
            message.success(`回测任务已完成：${row.taskId}`)
            continue
          }
          if (row.status.status === 'cancelled') {
            notifiedTaskStateRef.current[row.taskId] = statusText
            message.info(`回测任务已停止：${row.taskId}`)
            continue
          }
          if (row.status.status === 'failed') {
            notifiedTaskStateRef.current[row.taskId] = statusText
            message.error(row.status.error?.trim() || `回测任务失败：${row.taskId}`)
          }
          continue
        }

        const text = getErrorMessage(row.error)
        if (row.error instanceof ApiError && row.error.code === 'BACKTEST_TASK_NOT_FOUND') {
          markTaskFailed(row.taskId, '任务不存在或已失效（后端未找到任务记录）。', row.error.code)
          const failedKey = `failed:${row.error.code}`
          if (notifiedTaskStateRef.current[row.taskId] !== failedKey) {
            notifiedTaskStateRef.current[row.taskId] = failedKey
            message.error(`回测任务丢失：${row.taskId}`)
          }
          continue
        }

        const now = Date.now()
        const lastWarningAt = warningThrottleRef.current[row.taskId] ?? 0
        if (now - lastWarningAt >= TASK_TRANSIENT_WARNING_INTERVAL_MS) {
          warningThrottleRef.current[row.taskId] = now
          message.warning(`回测任务轮询异常（稍后重试）：${text}`)
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
  }, [activeTaskKey, activeTaskIds, markTaskFailed, message, upsertTaskStatus])

  return null
}
