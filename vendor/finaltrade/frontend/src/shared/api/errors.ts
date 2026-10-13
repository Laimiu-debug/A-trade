import { ApiError } from '@/shared/api/client'

/** 资源在后端不存在（重启丢内存、任务已删等） */
export function isApiNotFoundError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.code === 'HTTP_404') return true
  return error.code.endsWith('_NOT_FOUND')
}
