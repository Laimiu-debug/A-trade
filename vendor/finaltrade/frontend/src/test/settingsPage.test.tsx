import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { SettingsPage } from '@/pages/settings/SettingsPage'
import { renderWithProviders } from '@/test/renderWithProviders'

describe('SettingsPage', () => {
  it('restores dismissed tips via settings action', async () => {
    window.localStorage.setItem('tdx-dismissible-alert-v1.screener.strategy-info', '1')
    window.localStorage.setItem('tdx-dismissible-alert-v1.signals.strategy-info', '1')
    window.localStorage.setItem('non-dismissible-key', '1')

    renderWithProviders(<SettingsPage />, '/settings')

    const resetButton = await screen.findByRole('button', { name: '恢复已关闭提示条' })
    await userEvent.click(resetButton)

    await waitFor(() => {
      expect(window.localStorage.getItem('tdx-dismissible-alert-v1.screener.strategy-info')).toBeNull()
      expect(window.localStorage.getItem('tdx-dismissible-alert-v1.signals.strategy-info')).toBeNull()
    })
    expect(window.localStorage.getItem('non-dismissible-key')).toBe('1')
    expect(await screen.findByText('已恢复 2 条提示')).toBeInTheDocument()
  })
})

