/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import { useAIAssistantStore } from '@/state/aiAssistantStore'
import type { AIChatContext } from '@/types/contracts'

const PageAIContextState = createContext<AIChatContext | null>(null)

export function PageAIContextProvider({ children }: { children: ReactNode }) {
  const value = useMemo<AIChatContext>(
    () => ({
      page: 'generic',
      title: 'Final Trade',
    }),
    [],
  )
  return <PageAIContextState.Provider value={value}>{children}</PageAIContextState.Provider>
}

export function useRegisterPageAIContext(context: AIChatContext) {
  const setPageContext = useAIAssistantStore((state) => state.setPageContext)
  useEffect(() => {
    setPageContext(context)
  }, [context, setPageContext])
}

export function usePageAIContextSnapshot() {
  return useContext(PageAIContextState)
}
