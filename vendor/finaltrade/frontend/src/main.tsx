import { createElement, forwardRef } from 'react'
import { createRoot } from 'react-dom/client'
import { Typography } from 'antd'
import { App } from '@/app/App'
import './index.css'

function disableTypographyEllipsis() {
  const typography = Typography as unknown as Record<string, unknown>
  const targets = ['Text', 'Paragraph', 'Link']

  for (const key of targets) {
    const original = typography[key]
    if (typeof original !== 'function') continue
    const tagged = original as { __noEllipsisPatched?: boolean }
    if (tagged.__noEllipsisPatched) continue

    const wrapped = forwardRef<unknown, Record<string, unknown>>((props, ref) => {
      const nextProps = { ...props, ellipsis: false, ref }
      return createElement(original as never, nextProps)
    })
    ;(wrapped as unknown as { __noEllipsisPatched?: boolean }).__noEllipsisPatched = true
    ;(typography as Record<string, unknown>)[key] = wrapped
  }
}

async function enableMocking() {
  const shouldEnableMocking = import.meta.env.DEV && import.meta.env.VITE_ENABLE_MSW === 'true'
  if (shouldEnableMocking) {
    const { startMockWorker } = await import('@/mocks/browser')
    await startMockWorker()
  }
}

async function bootstrap() {
  disableTypographyEllipsis()
  await enableMocking()
  createRoot(document.getElementById('root')!).render(<App />)
}

void bootstrap()
