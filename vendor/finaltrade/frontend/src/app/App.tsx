import { AppProviders } from '@/app/providers'
import { AppRoutes } from '@/app/routes'
import { BacktestPlateauTaskWatcher } from '@/shared/components/BacktestPlateauTaskWatcher'
import { BacktestTaskWatcher } from '@/shared/components/BacktestTaskWatcher'
import { StaleResourceHydrator } from '@/shared/components/StaleResourceHydrator'

export function App() {
  return (
    <AppProviders>
      <StaleResourceHydrator />
      <BacktestTaskWatcher />
      <BacktestPlateauTaskWatcher />
      <AppRoutes />
    </AppProviders>
  )
}
