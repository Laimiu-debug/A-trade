import type { PropsWithChildren } from 'react'
import { App as AntApp, ConfigProvider } from 'antd'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { appTheme } from '@/shared/theme/theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 45_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
})

export function AppProviders({ children }: PropsWithChildren) {
  return (
    <ConfigProvider theme={appTheme}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>{children}</BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  )
}

