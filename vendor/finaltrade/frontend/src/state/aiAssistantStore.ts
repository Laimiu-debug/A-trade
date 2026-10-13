import { create } from 'zustand'
import { postAIChat, streamAIChat } from '@/shared/api/endpoints'
import type { AIChatContext, AIChatMessage, AIChatPage } from '@/types/contracts'

type AIAssistantState = {
  open: boolean
  loading: boolean
  streaming: boolean
  sessionId: string | null
  messages: AIChatMessage[]
  pageContext: AIChatContext
  setOpen: (open: boolean) => void
  setPageContext: (context: AIChatContext) => void
  sendMessage: (content: string) => Promise<void>
  clearSession: () => void
}

const DEFAULT_CONTEXT: AIChatContext = {
  page: 'generic',
  title: 'Final Trade',
}

let requestGeneration = 0
let activeController: AbortController | null = null

export const useAIAssistantStore = create<AIAssistantState>((set, get) => ({
  open: false,
  loading: false,
  streaming: false,
  sessionId: null,
  messages: [],
  pageContext: DEFAULT_CONTEXT,
  setOpen: (open) => set({ open }),
  setPageContext: (context) => set({ pageContext: context }),
  clearSession: () => {
    requestGeneration += 1
    activeController?.abort()
    activeController = null
    set({ sessionId: null, messages: [], loading: false, streaming: false })
  },
  sendMessage: async (content) => {
    const trimmed = content.trim()
    if (!trimmed) return
    const state = get()
    if (state.loading) return

    const generation = ++requestGeneration
    const controller = new AbortController()
    activeController = controller
    const isCurrent = () => generation === requestGeneration && !controller.signal.aborted
    const userMessage: AIChatMessage = { role: 'user', content: trimmed }
    const nextMessages = [...state.messages, userMessage]
    set({ loading: true, streaming: true, messages: nextMessages })

    let streamed = ''
    let streamFinished = false

    const finalizeSuccess = (sessionId: string, message: AIChatMessage) => {
      if (!isCurrent()) return
      streamFinished = true
      set({
        sessionId,
        messages: [...nextMessages, message],
        loading: false,
        streaming: false,
      })
    }

    const finalizeError = (text: string) => {
      if (!isCurrent()) return
      streamFinished = true
      set({
        messages: [...nextMessages, { role: 'assistant', content: text }],
        loading: false,
        streaming: false,
      })
    }

    try {
      await streamAIChat(
        {
          session_id: state.sessionId,
          messages: [userMessage],
          context: state.pageContext,
          stream: true,
        },
        {
          onDelta: (piece) => {
            if (!isCurrent()) return
            streamed += piece
            set({
              messages: [...nextMessages, { role: 'assistant', content: streamed }],
            })
          },
          onDone: (result) => {
            finalizeSuccess(result.session_id, result.message)
          },
          onError: () => {
            // fall through to non-stream fallback
          },
        },
        controller.signal,
      )

      if (isCurrent() && !streamFinished) {
        const response = await postAIChat({
          session_id: state.sessionId,
          messages: [userMessage],
          context: state.pageContext,
          stream: false,
        }, controller.signal)
        finalizeSuccess(response.session_id, response.message)
      }
    } catch (error) {
      if (!isCurrent()) return
      const detail = error instanceof Error && error.message.trim() ? error.message : ''
      finalizeError(
        detail
          ? `发送失败：${detail}。请检查后端是否启动、AI Provider 是否配置正确。`
          : '发送失败，请检查网络或 AI Provider 配置后重试。',
      )
    } finally {
      if (generation === requestGeneration) activeController = null
    }
  },
}))

export function buildPageTitle(page: AIChatPage, detail?: string) {
  const labelMap: Record<AIChatPage, string> = {
    chart: 'K线',
    screener: '选股漏斗',
    signals: '待买信号',
    signals_backtest: '待买回测',
    cross_validate: '策略交叉验证',
    backtest: '策略回测',
    strategy: '策略中心',
    event_judgment: '事件判别',
    review: '复盘',
    trade: '模拟交易',
    portfolio: '持仓管理',
    market_trend: '趋势龙头',
    sector_capital: '板块资金',
    abnormal_movement: '异动票',
    sentiment_valuation: '情绪估值',
    settings: '系统设置',
    ai_records: 'AI 分析',
    generic: 'Final Trade',
  }
  const base = labelMap[page] ?? 'Final Trade'
  return detail ? `${base} · ${detail}` : base
}
