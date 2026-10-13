import type { ScreenerStepConfigs } from '@/types/contracts'
import { normalizeScreenerStep1RankRange } from '@/shared/utils/screener'

export const SCREENER_PREFS_KEY = 'tdx-trend-screener-prefs-v1'

type ScreenerUserPrefs = {
  version: 1
  step_configs: ScreenerStepConfigs
}

function mergeStepConfigs(defaults: ScreenerStepConfigs, raw: Partial<ScreenerStepConfigs>): ScreenerStepConfigs {
  const step1Range = normalizeScreenerStep1RankRange({
    rank_start: raw.step1?.rank_start ?? defaults.step1.rank_start,
    top_n: raw.step1?.top_n ?? defaults.step1.top_n,
  })
  return {
    step1: {
      ...defaults.step1,
      ...step1Range,
      turnover_threshold: raw.step1?.turnover_threshold ?? defaults.step1.turnover_threshold,
      amount_threshold: raw.step1?.amount_threshold ?? defaults.step1.amount_threshold,
      amplitude_threshold: raw.step1?.amplitude_threshold ?? defaults.step1.amplitude_threshold,
    },
    step2: { ...defaults.step2, ...(raw.step2 ?? {}) },
    step3: { ...defaults.step3, ...(raw.step3 ?? {}) },
    step4: {
      ...defaults.step4,
      ...(raw.step4 ?? {}),
      allowed_theme_stages:
        raw.step4?.allowed_theme_stages && raw.step4.allowed_theme_stages.length > 0
          ? [...raw.step4.allowed_theme_stages]
          : [...defaults.step4.allowed_theme_stages],
    },
  }
}

/** Load persisted step configs; falls back to `defaults` when nothing saved. */
export function loadScreenerUserPrefs(defaults: ScreenerStepConfigs): ScreenerStepConfigs {
  if (typeof window === 'undefined') return defaults
  try {
    const raw = window.localStorage.getItem(SCREENER_PREFS_KEY)
    if (!raw) return defaults
    const parsed = JSON.parse(raw) as Partial<ScreenerUserPrefs>
    if (!parsed.step_configs || typeof parsed.step_configs !== 'object') return defaults
    return mergeStepConfigs(defaults, parsed.step_configs)
  } catch {
    return defaults
  }
}

export function saveScreenerUserPrefs(stepConfigs: ScreenerStepConfigs) {
  if (typeof window === 'undefined') return
  try {
    const payload: ScreenerUserPrefs = { version: 1, step_configs: stepConfigs }
    window.localStorage.setItem(SCREENER_PREFS_KEY, JSON.stringify(payload))
  } catch {
    // ignore quota / privacy mode
  }
}
