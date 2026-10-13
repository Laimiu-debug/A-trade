import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '@/shared/api/client'

describe('HTTP request lifetime', () => {
  afterEach(() => vi.unstubAllGlobals())

  it.each([200, 500])('times out while reading a stalled %s response body', async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start() {} }), { status })))
    await expect(apiRequest('/api/stalled', { timeoutMs: 25 })).rejects.toMatchObject({ code: 'REQUEST_TIMEOUT' })
  })

  it('cancels body processing after headers when the caller aborts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(new ReadableStream({ start() {} }))))
    const controller = new AbortController()
    const result = apiRequest('/api/stalled', { signal: controller.signal, timeoutMs: 5000 })
    const check = expect(result).rejects.toMatchObject({ code: 'REQUEST_ABORTED' })
    await Promise.resolve()
    controller.abort()
    await check
  })

  it('returns parsed data and preserves structured server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(Response.json({ ok: true })).mockResolvedValueOnce(Response.json({ code: 'STATE_SAVE_FAILED', message: '保存失败' }, { status: 500 })))
    await expect(apiRequest('/api/ok', { timeoutMs: 100 })).resolves.toEqual({ ok: true })
    await expect(apiRequest('/api/fail', { timeoutMs: 100 })).rejects.toMatchObject({ code: 'STATE_SAVE_FAILED', message: '保存失败' })
  })
})
