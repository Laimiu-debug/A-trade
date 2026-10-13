import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { App as AntdApp, Button, Drawer, Input, Tag, Typography } from 'antd'
import { RobotOutlined, SendOutlined, ToolOutlined, UserOutlined } from '@ant-design/icons'
import { getAIQuickPrompts, postAIParameterProposal } from '@/shared/api/endpoints'
import { BUILTIN_AI_PROMPTS, applyPromptPlaceholders } from '@/shared/ai/builtinPrompts'
import { getPageHint } from '@/shared/ai/routeAIContext'
import { useAIAssistantStore } from '@/state/aiAssistantStore'
import { useAIParameterApplyStore } from '@/state/aiParameterApplyStore'
import { ParameterDiffModal } from '@/shared/components/ParameterDiffModal'
import styles from './AIAssistantDrawer.module.css'

export function AIAssistantDrawer() {
  const { message } = AntdApp.useApp()
  const open = useAIAssistantStore((state) => state.open)
  const loading = useAIAssistantStore((state) => state.loading)
  const streaming = useAIAssistantStore((state) => state.streaming)
  const messages = useAIAssistantStore((state) => state.messages)
  const pageContext = useAIAssistantStore((state) => state.pageContext)
  const setOpen = useAIAssistantStore((state) => state.setOpen)
  const sendMessage = useAIAssistantStore((state) => state.sendMessage)
  const clearSession = useAIAssistantStore((state) => state.clearSession)
  const pendingProposal = useAIParameterApplyStore((state) => state.pendingProposal)
  const setPendingProposal = useAIParameterApplyStore((state) => state.setPendingProposal)
  const [draft, setDraft] = useState('')
  const [proposalLoading, setProposalLoading] = useState(false)
  const messagesRef = useRef<HTMLDivElement | null>(null)

  const quickPromptsQuery = useQuery({
    queryKey: ['ai-quick-prompts'],
    queryFn: getAIQuickPrompts,
    staleTime: 60_000,
  })

  const quickPrompts = useMemo(() => {
    const custom = quickPromptsQuery.data?.templates ?? []
    const page = pageContext.page
    const merged = [...BUILTIN_AI_PROMPTS, ...custom].filter((item) => item.page === page || item.page === 'generic')
    const pinned = merged.filter((item) => item.pinned)
    const rest = merged.filter((item) => !item.pinned)
    return [...pinned, ...rest].slice(0, 10)
  }, [pageContext.page, quickPromptsQuery.data?.templates])

  const canSuggestParams = pageContext.page === 'backtest' || pageContext.page === 'screener'
  const pageHint = getPageHint(pageContext.page)

  useEffect(() => {
    if (!open) setDraft('')
  }, [open])

  useEffect(() => {
    if (!messagesRef.current) return
    messagesRef.current.scrollTop = messagesRef.current.scrollHeight
  }, [messages, loading, open])

  async function handleSend(text?: string) {
    const content = (text ?? draft).trim()
    if (!content) return
    await sendMessage(content)
    setDraft('')
  }

  async function handleParameterProposal() {
    setProposalLoading(true)
    try {
      const proposal = await postAIParameterProposal(
        '根据当前页面运行结果，给出可执行的参数调整建议（JSON changes）。',
        pageContext,
      )
      setPendingProposal(proposal)
    } catch {
      message.error('生成参数建议失败')
    } finally {
      setProposalLoading(false)
    }
  }

  return (
    <>
      <Drawer
        className={styles.drawer}
        title={null}
        closable
        placement="right"
        width={460}
        open={open}
        onClose={() => setOpen(false)}
        styles={{ body: { padding: 0 } }}
      >
        <div className={styles.shell}>
          <div className={styles.header}>
            <div className={styles.headerTitleRow}>
              <div className={styles.headerIcon}>
                <RobotOutlined />
              </div>
              <div>
                <p className={styles.headerTitle}>AI 助手</p>
                <p className={styles.headerSubtitle}>基于当前页面上下文回答</p>
              </div>
              <Button
                size="small"
                style={{ marginLeft: 'auto' }}
                onClick={() => {
                  clearSession()
                  message.success('已清空对话')
                }}
              >
                清空
              </Button>
            </div>
            <div className={styles.contextRow}>
              <Tag color="processing" className={styles.contextTag}>{pageContext.title || 'Final Trade'}</Tag>
              {pageContext.symbol ? <Tag className={styles.contextTag}>{pageContext.symbol.toUpperCase()}</Tag> : null}
            </div>
          </div>

          {quickPrompts.length > 0 || canSuggestParams ? (
            <div className={styles.promptSection}>
              <span className={styles.promptLabel}>快捷提问</span>
              <div className={styles.promptList}>
                {quickPrompts.map((item) => (
                  <Button
                    key={item.id}
                    size="small"
                    className={styles.promptChip}
                    disabled={loading}
                    onClick={() => void handleSend(applyPromptPlaceholders(item.prompt, pageContext.symbol))}
                  >
                    {item.label}
                  </Button>
                ))}
                {canSuggestParams ? (
                  <Button
                    size="small"
                    className={styles.promptChip}
                    icon={<ToolOutlined />}
                    loading={proposalLoading}
                    disabled={loading}
                    onClick={() => void handleParameterProposal()}
                  >
                    设参建议
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}

          <div ref={messagesRef} className={styles.messages}>
            {messages.length === 0 ? (
              <div className={styles.emptyState}>
                <div className={styles.emptyIcon}>
                  <RobotOutlined />
                </div>
                <p className={styles.emptyTitle}>有什么可以帮你？</p>
                <p className={styles.emptyHint}>{pageHint}</p>
              </div>
            ) : (
              messages.map((item, index) => {
                const isUser = item.role === 'user'
                const isThinking =
                  !isUser
                  && loading
                  && index === messages.length - 1
                  && !item.content
                const content = item.content || (isThinking ? '思考中…' : '（空回复）')
                return (
                  <div
                    key={`${item.role}-${index}`}
                    className={`${styles.messageRow} ${isUser ? styles.messageRowUser : ''}`}
                  >
                    <div className={`${styles.avatar} ${isUser ? styles.avatarUser : styles.avatarAssistant}`}>
                      {isUser ? <UserOutlined /> : <RobotOutlined />}
                    </div>
                    <div
                      className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant} ${isThinking ? styles.bubbleThinking : ''}`}
                    >
                      {content}
                      {streaming && loading && index === messages.length - 1 && !isUser && item.content ? '▍' : ''}
                    </div>
                  </div>
                )
              })
            )}
          </div>

          <div className={styles.composer}>
            <div className={styles.composerRow}>
              <Input.TextArea
                className={styles.composerInput}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="输入问题… Enter 发送，Shift+Enter 换行"
                autoSize={{ minRows: 2, maxRows: 5 }}
                disabled={loading}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    void handleSend()
                  }
                }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={loading}
                onClick={() => void handleSend()}
              >
                发送
              </Button>
            </div>
            <Typography.Paragraph className={styles.composerHint}>
              回答基于 PLAYBOOK 与当前页面 RUNTIME 数据，不构成投资建议。
            </Typography.Paragraph>
          </div>
        </div>
      </Drawer>

      <ParameterDiffModal
        proposal={pendingProposal}
        open={Boolean(pendingProposal)}
        onClose={() => setPendingProposal(null)}
      />
    </>
  )
}

export function AIAssistantLauncher() {
  const setOpen = useAIAssistantStore((state) => state.setOpen)
  return (
    <Button type="default" className={styles.launcher} icon={<RobotOutlined />} onClick={() => setOpen(true)}>
      AI 助手
    </Button>
  )
}
