import { screen } from '@testing-library/react'
import { delay, http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import { server } from '@/mocks/server'
import { ReviewPage } from '@/pages/review/ReviewPage'
import { renderWithProviders } from '@/test/renderWithProviders'

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echart" />,
}))

describe('ReviewPage', () => {
  it('renders review, fills and portfolio data', async () => {
    server.use(
      http.get('/api/review/stats', async () => {
        await delay(10)
        return HttpResponse.json({
          stats: {
            win_rate: 0.6,
            total_return: 0.12,
            max_drawdown: 0.08,
            avg_pnl_ratio: 0.03,
            trade_count: 5,
            win_count: 3,
            loss_count: 2,
            profit_factor: 1.8,
          },
          trades: [
            {
              symbol: 'sz300750',
              buy_date: '2026-01-01',
              buy_price: 160,
              sell_date: '2026-01-10',
              sell_price: 170,
              quantity: 100,
              holding_days: 9,
              pnl_amount: 900,
              pnl_ratio: 0.056,
            },
          ],
          equity_curve: [
            { date: '2026-01-01', equity: 1_000_000, realized_pnl: 0 },
            { date: '2026-01-10', equity: 1_000_900, realized_pnl: 900 },
          ],
          drawdown_curve: [
            { date: '2026-01-01', drawdown: 0 },
            { date: '2026-01-10', drawdown: -0.01 },
          ],
          monthly_returns: [{ month: '2026-01', return_ratio: 0.009, pnl_amount: 900, trade_count: 1 }],
          top_trades: [
            {
              symbol: 'sz300750',
              buy_date: '2026-01-01',
              buy_price: 160,
              sell_date: '2026-01-10',
              sell_price: 170,
              quantity: 100,
              holding_days: 9,
              pnl_amount: 900,
              pnl_ratio: 0.056,
            },
          ],
          bottom_trades: [
            {
              symbol: 'sz300750',
              buy_date: '2026-01-01',
              buy_price: 160,
              sell_date: '2026-01-10',
              sell_price: 170,
              quantity: 100,
              holding_days: 9,
              pnl_amount: 900,
              pnl_ratio: 0.056,
            },
          ],
          cost_snapshot: {
            initial_capital: 1_000_000,
            commission_rate: 0.0003,
            min_commission: 5,
            stamp_tax_rate: 0.001,
            transfer_fee_rate: 0.00001,
            slippage_rate: 0,
          },
          range: {
            date_from: '2025-11-15',
            date_to: '2026-02-13',
            date_axis: 'sell',
          },
        })
      }),
      http.get('/api/sim/fills', async () => {
        await delay(10)
        return HttpResponse.json({
          items: [
            {
              order_id: 'ord-1',
              symbol: 'sz300750',
              side: 'buy',
              quantity: 100,
              fill_date: '2026-01-02',
              fill_price: 160,
              price_source: 'vwap',
              gross_amount: 16_000,
              net_amount: -16_005,
              fee_commission: 5,
              fee_stamp_tax: 0,
              fee_transfer: 0,
            },
          ],
          total: 1,
          page: 1,
          page_size: 500,
        })
      }),
      http.get('/api/sim/portfolio', async () => {
        await delay(10)
        return HttpResponse.json({
          as_of_date: '2026-02-13',
          total_asset: 1_005_000,
          cash: 900_000,
          position_value: 105_000,
          realized_pnl: 900,
          unrealized_pnl: 500,
          pending_order_count: 0,
          positions: [],
        })
      }),
    )

    renderWithProviders(<ReviewPage />, '/review')

    expect(await screen.findByText('已平仓笔数')).toBeInTheDocument()
    expect(await screen.findByText('买入成交笔数')).toBeInTheDocument()
    expect((await screen.findAllByText('sz300750')).length).toBeGreaterThan(0)
  }, 10_000)
})
