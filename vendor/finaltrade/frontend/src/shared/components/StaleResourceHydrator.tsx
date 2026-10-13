import { useEffect, useRef } from 'react'
import { getScreenerRun, listBacktestPlateauTasks } from '@/shared/api/endpoints'
import { isApiNotFoundError } from '@/shared/api/errors'
import { collectCachedScreenerRunIds, clearScreenerRunIdFromCaches } from '@/shared/screenerRunCache'
import { useBacktestPlateauTaskStore } from '@/state/backtestPlateauTaskStore'

/**
 * 应用启动时校验 localStorage 中的任务/run_id，移除后端已不存在的记录，减少控制台 404。
 */
export function StaleResourceHydrator() {
  const ranRef = useRef(false)

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true

    let cancelled = false

    ;(async () => {
      try {
        const listed = await listBacktestPlateauTasks()
        if (cancelled) return
        const valid = new Set(listed.items.map((item) => item.task_id))
        const { tasksById, activeTaskIds, removeTask } = useBacktestPlateauTaskStore.getState()
        const staleIds = new Set<string>()
        for (const id of Object.keys(tasksById)) {
          if (!valid.has(id)) staleIds.add(id)
        }
        for (const id of activeTaskIds) {
          if (!valid.has(id)) staleIds.add(id)
        }
        for (const id of staleIds) {
          removeTask(id)
        }
      } catch {
        // 列表失败时不强行删本地任务，交给轮询兜底
      }

      const runIds = collectCachedScreenerRunIds()
      for (const runId of runIds) {
        if (cancelled) return
        try {
          await getScreenerRun(runId)
        } catch (error) {
          if (!isApiNotFoundError(error)) continue
          clearScreenerRunIdFromCaches(runId)
        }
      }
    })()

    return () => {
      cancelled = true
    }
  }, [])

  return null
}
