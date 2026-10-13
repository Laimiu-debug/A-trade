import { describe, expect, it } from 'vitest'
import { loadScreenerUserPrefs, saveScreenerUserPrefs, SCREENER_PREFS_KEY } from '@/shared/utils/screenerPrefs'
import type { ScreenerStepConfigs } from '@/types/contracts'

const defaults: ScreenerStepConfigs = {
  step1: {
    rank_start: 1,
    top_n: 500,
    turnover_threshold: 0.05,
    amount_threshold: 5e8,
    amplitude_threshold: 0.03,
  },
  step2: {
    retrace_min: 0.05,
    retrace_max: 0.25,
    max_pullback_days: 3,
    min_ma10_above_ma20_days: 5,
    min_ma5_above_ma10_days: 3,
    max_price_vs_ma20: 0.08,
    require_above_ma20: true,
    allow_b_trend: false,
  },
  step3: {
    min_vol_slope20: 0.05,
    min_up_down_volume_ratio: 1.3,
    max_pullback_volume_ratio: 0.9,
    allow_blowoff_top: false,
    allow_divergence_5d: false,
    allow_upper_shadow_risk: false,
    allow_degraded: false,
  },
  step4: {
    final_top_n: 8,
    min_ai_confidence: 0.55,
    allowed_theme_stages: ['发酵中', '高潮'],
    allow_degraded: true,
  },
}

describe('screenerPrefs', () => {
  it('persists step1 top_n across load', () => {
    saveScreenerUserPrefs({
      ...defaults,
      step1: { ...defaults.step1, top_n: 100 },
    })
    const loaded = loadScreenerUserPrefs(defaults)
    expect(loaded.step1.top_n).toBe(100)
    window.localStorage.removeItem(SCREENER_PREFS_KEY)
  })

  it('falls back to defaults when prefs missing', () => {
    window.localStorage.removeItem(SCREENER_PREFS_KEY)
    const loaded = loadScreenerUserPrefs(defaults)
    expect(loaded.step1.top_n).toBe(500)
  })
})
