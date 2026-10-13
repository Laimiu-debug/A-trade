import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useAIAssistantStore } from '@/state/aiAssistantStore'
import { buildRouteAIContext } from '@/shared/ai/routeAIContext'

export function useRouteAIContextSync() {
  const location = useLocation()
  const setPageContext = useAIAssistantStore((state) => state.setPageContext)

  useEffect(() => {
    setPageContext(buildRouteAIContext(location.pathname))
  }, [location.pathname, setPageContext])
}
