const SCREENER_CACHE_KEY = 'tdx-trend-screener-cache-v6'
const SIGNAL_RUN_CACHE_KEY = 'tdx-signals-run-id-v1'

function readJson(raw: string | null): Record<string, unknown> | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as unknown
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** 从筛选页缓存与信号页 run_id 缓存收集可能过期的 run_id */
export function collectCachedScreenerRunIds(): string[] {
  const ids = new Set<string>()
  try {
    const direct = (window.localStorage.getItem(SIGNAL_RUN_CACHE_KEY) ?? '').trim()
    if (direct) ids.add(direct)
  } catch {
    // ignore
  }
  try {
    const parsed = readJson(window.localStorage.getItem(SCREENER_CACHE_KEY))
    const runMeta = parsed?.run_meta
    if (runMeta && typeof runMeta === 'object') {
      const runId = (runMeta as { runId?: unknown }).runId
      if (typeof runId === 'string' && runId.trim()) ids.add(runId.trim())
    }
  } catch {
    // ignore
  }
  return [...ids]
}

/** 后端 404 时清除本地 run_id，避免反复请求 */
export function clearScreenerRunIdFromCaches(runId: string) {
  const trimmed = runId.trim()
  if (!trimmed) return

  try {
    const cached = (window.localStorage.getItem(SIGNAL_RUN_CACHE_KEY) ?? '').trim()
    if (cached === trimmed) {
      window.localStorage.removeItem(SIGNAL_RUN_CACHE_KEY)
    }
  } catch {
    // ignore
  }

  try {
    const parsed = readJson(window.localStorage.getItem(SCREENER_CACHE_KEY))
    if (!parsed) return
    const runMeta = parsed.run_meta
    if (!runMeta || typeof runMeta !== 'object') return
    const currentRunId = (runMeta as { runId?: unknown }).runId
    if (typeof currentRunId !== 'string' || currentRunId.trim() !== trimmed) return
    const next = { ...parsed, run_meta: undefined }
    window.localStorage.setItem(SCREENER_CACHE_KEY, JSON.stringify(next))
  } catch {
    // ignore
  }
}
