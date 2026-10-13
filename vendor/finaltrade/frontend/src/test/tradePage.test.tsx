import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { TradePage } from '@/pages/trade/TradePage'
import { renderWithProviders } from '@/test/renderWithProviders'

describe('TradePage', () => {
  it('submits order and shows pending row', async () => {
    renderWithProviders(<TradePage />, '/trade')

    const submitButton = await screen.findByRole('button', { name: '提交订单' })
    await userEvent.click(submitButton)

    await waitFor(() => {
      expect(screen.getByText('pending')).toBeInTheDocument()
    }, { timeout: 10_000 })
  }, 15_000)
})
