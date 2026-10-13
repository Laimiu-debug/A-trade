import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildRouteAIContext } from '@/shared/ai/routeAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import type { AIChatContext } from '@/types/contracts'

export function usePageAIContextRegistration(
  payload?: Record<string, unknown>,
  extras?: Partial<Omit<AIChatContext, 'payload'>>,
) {
  const location = useLocation()
  const context = useMemo(() => {
    const base = buildRouteAIContext(location.pathname)
    const page = extras?.page ?? base.page
    const title = extras?.title ?? (extras?.symbol ? buildPageTitle(page, extras.symbol) : base.title)
    return {
      ...base,
      ...extras,
      page,
      title,
      payload: {
        ...base.payload,
        ...payload,
      },
    } satisfies AIChatContext
  }, [extras, location.pathname, payload])

  useRegisterPageAIContext(context)
}
