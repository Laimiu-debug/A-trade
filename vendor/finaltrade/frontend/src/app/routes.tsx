import { Suspense, lazy } from 'react'
import { Spin } from 'antd'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/shared/components/AppShell'

const ScreenerPage = lazy(async () => {
  const module = await import('@/pages/screener/ScreenerPage')
  return { default: module.ScreenerPage }
})
const ChartPage = lazy(async () => {
  const module = await import('@/pages/chart/ChartPage')
  return { default: module.ChartPage }
})
const SignalsPage = lazy(async () => {
  const module = await import('@/pages/signals/SignalsPage')
  return { default: module.SignalsPage }
})
const SignalsBacktestPage = lazy(async () => {
  const module = await import('@/pages/signals/SignalsBacktestPage')
  return { default: module.SignalsBacktestPage }
})
const CrossValidatePage = lazy(async () => {
  const module = await import('@/pages/cross-validate/CrossValidatePage')
  return { default: module.CrossValidatePage }
})
const MarketTrendPage = lazy(async () => {
  const module = await import('@/pages/market-trend/MarketTrendPage')
  return { default: module.MarketTrendPage }
})
const SectorCapitalPage = lazy(async () => {
  const module = await import('@/pages/sector-capital/SectorCapitalPage')
  return { default: module.SectorCapitalPage }
})
const AbnormalMovementPage = lazy(async () => {
  const module = await import('@/pages/abnormal-movement/AbnormalMovementPage')
  return { default: module.AbnormalMovementPage }
})
const SentimentValuationPage = lazy(async () => {
  const module = await import('@/pages/sentiment-valuation/SentimentValuationPage')
  return { default: module.SentimentValuationPage }
})
const TradePage = lazy(async () => {
  const module = await import('@/pages/trade/TradePage')
  return { default: module.TradePage }
})
const PortfolioPage = lazy(async () => {
  const module = await import('@/pages/portfolio/PortfolioPage')
  return { default: module.PortfolioPage }
})
const ReviewPage = lazy(async () => {
  const module = await import('@/pages/review/ReviewPage')
  return { default: module.ReviewPage }
})
const BacktestPage = lazy(async () => {
  const module = await import('@/pages/backtest/BacktestPage')
  return { default: module.BacktestPage }
})
const ReviewSharePage = lazy(async () => {
  const module = await import('@/pages/review/ReviewSharePage')
  return { default: module.ReviewSharePage }
})
const ReviewNewsPage = lazy(async () => {
  const module = await import('@/pages/review/ReviewNewsPage')
  return { default: module.ReviewNewsPage }
})
const AiPage = lazy(async () => {
  const module = await import('@/pages/ai/AiPage')
  return { default: module.AiPage }
})
const SettingsPage = lazy(async () => {
  const module = await import('@/pages/settings/SettingsPage')
  return { default: module.SettingsPage }
})
const NotFoundPage = lazy(async () => {
  const module = await import('@/pages/not-found/NotFoundPage')
  return { default: module.NotFoundPage }
})
const StrategyCenterPage = lazy(async () => {
  const module = await import('@/pages/strategy/StrategyCenterPage')
  return { default: module.StrategyCenterPage }
})
const EventJudgmentPage = lazy(async () => {
  const module = await import('@/pages/strategy/EventJudgmentPage')
  return { default: module.EventJudgmentPage }
})

const routeLoadingFallback = (
  <div className="route-loading-fallback">
    <Spin size="large" />
  </div>
)

export function AppRoutes() {
  return (
    <Suspense fallback={routeLoadingFallback}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/screener" replace />} />
          <Route path="/screener" element={<ScreenerPage />} />
          <Route path="/stocks/:symbol/chart" element={<ChartPage />} />
          <Route path="/signals" element={<SignalsPage />} />
          <Route path="/signals/backtest" element={<SignalsBacktestPage />} />
          <Route path="/signals/cross-validate" element={<CrossValidatePage />} />
          <Route path="/market/trend" element={<MarketTrendPage />} />
          <Route path="/market/sector-capital" element={<SectorCapitalPage />} />
          <Route path="/market/abnormal-movement" element={<AbnormalMovementPage />} />
          <Route path="/market/sentiment-valuation" element={<SentimentValuationPage />} />
          <Route path="/strategy" element={<StrategyCenterPage />} />
          <Route path="/strategy/events" element={<EventJudgmentPage />} />
          <Route path="/trade" element={<TradePage />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/review/share" element={<ReviewSharePage />} />
          <Route path="/review/news" element={<ReviewNewsPage />} />
          <Route path="/ai" element={<AiPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
