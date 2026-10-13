import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { delay, http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '@/mocks/server'
import { PortfolioPage } from '@/pages/portfolio/PortfolioPage'
import { renderWithProviders } from '@/test/renderWithProviders'

describe('PortfolioPage', () => {
  it('opens quick sell modal from position row', async () => {
    server.use(
      http.get('/api/sim/portfolio', async () => {
        await delay(10)
        return HttpResponse.json({
          as_of_date: '2026-02-13',
          total_asset: 1_020_000,
          cash: 500_000,
          position_value: 520_000,
          realized_pnl: 12_000,
          unrealized_pnl: 8_000,
          pending_order_count: 0,
          positions: [
            {
              symbol: 'sz300750',
              name: '宁德时代',
              quantity: 1000,
              available_quantity: 1000,
              avg_cost: 165.3,
              current_price: 174.2,
              market_value: 174_200,
              pnl_amount: 8_900,
              pnl_ratio: 0.0538,
              holding_days: 9,
            },
          ],
        })
      }),
    )

    renderWithProviders(<PortfolioPage />, '/portfolio')

    await screen.findByText('sz300750')
    await userEvent.click(screen.getByRole('button', { name: '快捷卖出' }))

    await waitFor(() => {
      expect(screen.getByText('快捷卖出 sz300750')).toBeInTheDocument()
    })
  })
})
