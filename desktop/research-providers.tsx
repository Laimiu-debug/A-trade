import type { PropsWithChildren } from 'react'
import { App as AntApp, ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { useDesktopContext } from './research-bridge'
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 15000, retry: 1, refetchOnWindowFocus: false }, mutations: { retry: 0 } } })
export function AppProviders({ children }: PropsWithChildren) {
  const context = useDesktopContext()
  return <ConfigProvider locale={zhCN} button={{autoInsertSpace:false}} componentSize={context.compact ? 'small' : 'middle'} theme={{ algorithm: theme.defaultAlgorithm, token: {
    colorPrimary: '#2457e5', colorInfo: '#2457e5', colorSuccess: '#15803d', colorWarning: '#a16207', colorError: '#c7383f',
    colorText: '#172333', colorTextSecondary: '#758195', colorBgContainer: '#ffffff', colorBorder: '#dfe5ef', colorBorderSecondary: '#e5eaf2',
    borderRadius: 6, borderRadiusLG: 10, fontSize: context.largeText ? 16 : 14, motion: !(context.reduced || window.matchMedia('(prefers-reduced-motion: reduce)').matches),
    fontFamily: '"Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif', controlHeight: context.compact ? 30 : 34,
  }, components: { Card: { headerFontSize: 14, paddingLG: 20 }, Table: { headerBg: '#f7f9fc', cellPaddingBlock: context.compact ? 8 : 12 }, Button: { primaryShadow: '0 2px 3px #2457e516' } } }}>
    <AntApp><QueryClientProvider client={queryClient}><BrowserRouter>{children}</BrowserRouter></QueryClientProvider></AntApp>
  </ConfigProvider>
}
