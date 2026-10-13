import { screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StrategyCenterPage } from '@/pages/strategy/StrategyCenterPage'
import { renderWithProviders } from '@/test/renderWithProviders'

type CompareParamCategory = 'all' | 'scoring' | 'event' | 'gate' | 'execution' | 'risk' | 'other'

function encodeUtf8ToBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function buildCompareViewShareCode(snapshot: {
  strategy_ids: string[]
  only_differences: boolean
  category: CompareParamCategory
}): string {
  const payload = {
    schema_version: 'compare_view_share.v1',
    snapshot,
  }
  return `ftcv1.${encodeUtf8ToBase64Url(JSON.stringify(payload))}`
}

describe('StrategyCenterPage', () => {
  it('restores compare share code from cv query', async () => {
    const shareCode = buildCompareViewShareCode({
      strategy_ids: ['wyckoff_trend_v1', 'wyckoff_trend_v2'],
      only_differences: false,
      category: 'risk',
    })

    renderWithProviders(<StrategyCenterPage />, `/strategy?cv=${encodeURIComponent(shareCode)}`)

    const shareCodeInput = await screen.findByPlaceholderText('粘贴分享码后可恢复对比视图')
    await waitFor(() => {
      expect(shareCodeInput).toHaveValue(shareCode)
    })
  })

  it('clears invalid cv query and shows warning', async () => {
    renderWithProviders(<StrategyCenterPage />, '/strategy?cv=invalid-code')

    expect(await screen.findByText('链接中的对比视图分享参数无效，已自动清理。')).toBeInTheDocument()
    const shareCodeInput = await screen.findByPlaceholderText('粘贴分享码后可恢复对比视图')
    expect(shareCodeInput).toHaveValue('')
  })
})

