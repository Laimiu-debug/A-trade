import { beforeEach, describe, expect, it, vi } from 'vitest'
import { postAIChat, streamAIChat } from '@/shared/api/endpoints'
import { useAIAssistantStore } from '@/state/aiAssistantStore'

vi.mock('@/shared/api/endpoints', () => ({
  postAIChat: vi.fn(),
  streamAIChat: vi.fn(),
}))

describe('aiAssistantStore', () => {
  beforeEach(() => {
    useAIAssistantStore.getState().clearSession()
    useAIAssistantStore.setState({
      open: false,
      loading: false,
      streaming: false,
      sessionId: null,
      messages: [],
      pageContext: { page: 'generic', title: 'Final Trade' },
    })
    vi.mocked(streamAIChat).mockReset()
    vi.mocked(postAIChat).mockReset()
  })

  it('clears loading state when session is cleared', () => {
    useAIAssistantStore.setState({ loading: true, streaming: true })
    useAIAssistantStore.getState().clearSession()
    const state = useAIAssistantStore.getState()
    expect(state.loading).toBe(false)
    expect(state.streaming).toBe(false)
    expect(state.messages).toEqual([])
  })

  it('falls back to non-stream chat when stream does not finish', async () => {
    vi.mocked(streamAIChat).mockImplementation(async (_payload, handlers) => {
      handlers.onError?.('流式响应未完成')
    })
    vi.mocked(postAIChat).mockResolvedValue({
      session_id: 'sess-1',
      message: { role: 'assistant', content: 'fallback reply' },
    })

    await useAIAssistantStore.getState().sendMessage('hello')

    const state = useAIAssistantStore.getState()
    expect(postAIChat).toHaveBeenCalledTimes(1)
    expect(state.loading).toBe(false)
    expect(state.messages.at(-1)).toEqual({ role: 'assistant', content: 'fallback reply' })
  })

  it('blocks concurrent sends while loading', async () => {
    vi.mocked(streamAIChat).mockImplementation(
      () => new Promise(() => {
        // never resolves
      }),
    )

    void useAIAssistantStore.getState().sendMessage('first')
    await useAIAssistantStore.getState().sendMessage('second')

    expect(streamAIChat).toHaveBeenCalledTimes(1)
    expect(useAIAssistantStore.getState().messages.filter((item) => item.role === 'user')).toHaveLength(1)
  })

  it('aborts a cleared request and ignores its callbacks during a new request', async () => {
    let releaseOld: () => void = () => {}
    let releaseNew: () => void = () => {}
    let oldHandlers: Parameters<typeof streamAIChat>[1] | undefined
    let oldSignal: AbortSignal | undefined
    vi.mocked(streamAIChat)
      .mockImplementationOnce((_payload, handlers, signal) => {
        oldHandlers = handlers
        oldSignal = signal
        return new Promise<void>((resolve) => { releaseOld = resolve })
      })
      .mockImplementationOnce(() => new Promise<void>((resolve) => { releaseNew = resolve }))
    vi.mocked(postAIChat).mockResolvedValue({ session_id: 'new-session', message: { role: 'assistant', content: 'new reply' } })
    const oldRequest = useAIAssistantStore.getState().sendMessage('old prompt')
    useAIAssistantStore.getState().clearSession()
    expect(oldSignal?.aborted).toBe(true)
    const newRequest = useAIAssistantStore.getState().sendMessage('new prompt')
    oldHandlers?.onDelta('old delta')
    oldHandlers?.onDone({ session_id: 'old-session', message: { role: 'assistant', content: 'old reply' } })
    releaseOld()
    await oldRequest
    expect(useAIAssistantStore.getState().messages).toEqual([{ role: 'user', content: 'new prompt' }])
    expect(useAIAssistantStore.getState().loading).toBe(true)
    expect(useAIAssistantStore.getState().sessionId).toBeNull()
    expect(postAIChat).not.toHaveBeenCalled()
    releaseNew()
    await newRequest
    expect(useAIAssistantStore.getState().sessionId).toBe('new-session')
    expect(useAIAssistantStore.getState().loading).toBe(false)
  })

  it('does not restore cleared messages after an old request rejects', async () => {
    let rejectOld: (error: Error) => void = () => {}
    vi.mocked(streamAIChat).mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectOld = reject }))
    const request = useAIAssistantStore.getState().sendMessage('old prompt')
    useAIAssistantStore.getState().clearSession()
    rejectOld(new Error('late failure'))
    await request
    expect(useAIAssistantStore.getState().messages).toEqual([])
    expect(postAIChat).not.toHaveBeenCalled()
  })
})
