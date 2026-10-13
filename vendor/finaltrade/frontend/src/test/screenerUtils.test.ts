import { describe, expect, it } from 'vitest'
import type { ScreenerResult } from '@/types/contracts'
import {
  formatScreenerRankRange,
  getLocalDayKey,
  getScreenerPathState,
  isScreenerCacheMarketDataFresh,
  normalizeScreenerStep1RankRange,
  sliceRowsByScreenerRankRange,
} from '@/shared/utils/screener'

function buildRow(overrides: Partial<ScreenerResult> = {}): ScreenerResult {
  return {
    symbol: 'sz300750',
    name: '宁德时代',
    latest_price: 100,
    day_change: 1,
    day_change_pct: 0.01,
    score: 80,
    ret40: 0.25,
    turnover20: 0.08,
    amount20: 8e8,
    amplitude20: 0.05,
    retrace20: 0.05,
    pullback_days: 2,
    ma10_above_ma20_days: 8,
    ma5_above_ma10_days: 6,
    price_vs_ma20: 0.04,
    vol_slope20: 0.1,
    up_down_volume_ratio: 1.5,
    pullback_volume_ratio: 0.8,
    has_blowoff_top: false,
    has_divergence_5d: false,
    has_upper_shadow_risk: false,
    ai_confidence: 0.7,
    theme_stage: '发酵中',
    trend_class: 'A',
    stage: 'Mid',
    labels: [],
    reject_reasons: [],
    degraded: false,
    ...overrides,
  }
}

describe('screener utils', () => {
  it('detects stale screener cache across calendar days', () => {
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(yesterday.getDate() - 1)
    expect(getLocalDayKey(today)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(isScreenerCacheMarketDataFresh(today.toISOString(), today)).toBe(true)
    expect(isScreenerCacheMarketDataFresh(yesterday.toISOString(), today)).toBe(false)
  })

  it('normalizes invalid rank windows', () => {
    expect(normalizeScreenerStep1RankRange({ rank_start: 200, top_n: 100 })).toEqual({
      rank_start: 200,
      top_n: 200,
    })
    expect(formatScreenerRankRange({ rank_start: 100, top_n: 200 })).toBe('第100-200名')
  })

  it('slices rows by rank window', () => {
    expect(sliceRowsByScreenerRankRange([1, 2, 3, 4, 5], { rank_start: 2, top_n: 4 })).toEqual([2, 3, 4])
  })

  it('classifies rising path and overheat pullback distinctly', () => {
    expect(getScreenerPathState(buildRow())).toBe('上涨路径中')
    expect(getScreenerPathState(buildRow({
      stage: 'Late',
      retrace20: 0.16,
      pullback_days: 5,
      has_blowoff_top: true,
      theme_stage: '高潮',
    }))).toBe('高位回撤')
    expect(getScreenerPathState(buildRow({
      retrace20: 0.12,
      pullback_days: 4,
      pullback_volume_ratio: 0.92,
    }))).toBe('回踩蓄势')
  })
})
