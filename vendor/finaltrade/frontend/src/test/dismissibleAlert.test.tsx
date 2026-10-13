import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { DismissibleAlert, resetAllDismissedAlerts } from '@/shared/components/DismissibleAlert'

describe('DismissibleAlert', () => {
  it('remembers close state and can be restored by global reset', async () => {
    const { container } = render(
      <DismissibleAlert
        dismissKey="unit.tip"
        type="info"
        title="可关闭提示"
      />,
    )

    expect(screen.getByText('可关闭提示')).toBeInTheDocument()

    const closeButton = container.querySelector('button.ant-alert-close-icon')
    if (!closeButton) throw new Error('close button not found')
    await userEvent.click(closeButton)

    expect(screen.queryByText('可关闭提示')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('tdx-dismissible-alert-v1.unit.tip')).toBe('1')

    act(() => {
      expect(resetAllDismissedAlerts()).toBe(1)
    })
    expect(window.localStorage.getItem('tdx-dismissible-alert-v1.unit.tip')).toBeNull()
    expect(await screen.findByText('可关闭提示')).toBeInTheDocument()
  })

  it('only clears keys under dismissible alert prefix', () => {
    window.localStorage.setItem('tdx-dismissible-alert-v1.tip-a', '1')
    window.localStorage.setItem('tdx-dismissible-alert-v1.tip-b', '1')
    window.localStorage.setItem('other-storage-key', '1')

    expect(resetAllDismissedAlerts()).toBe(2)
    expect(window.localStorage.getItem('tdx-dismissible-alert-v1.tip-a')).toBeNull()
    expect(window.localStorage.getItem('tdx-dismissible-alert-v1.tip-b')).toBeNull()
    expect(window.localStorage.getItem('other-storage-key')).toBe('1')
  })
})
