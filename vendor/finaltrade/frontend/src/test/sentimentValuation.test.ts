import { describe, expect, it } from 'vitest'
import {
  VALUATION_PRESETS,
  calcTheoreticalCap,
  growthRateToCoef,
  indexPointsToCoef,
  presetToFormValues,
} from '@/shared/utils/sentimentValuation'

describe('sentimentValuation', () => {
  it('matches changjiang power example', () => {
    const result = calcTheoreticalCap({
      symbol: 'sh600900',
      name: '长江电力',
      earningsYi: 358,
      growthRatePct: 5,
      basePe: 12.5,
      indexPoints: 4090,
      sentimentCoef: 1,
      actualCapYi: 6523,
    })
    expect(result.theoreticalCapYi).toBeGreaterThan(6380)
    expect(result.theoreticalCapYi).toBeLessThan(6420)
  })

  it('derives implied sentiment for yuanjie', () => {
    const result = calcTheoreticalCap({
      symbol: 'sh688498',
      name: '源杰科技',
      earningsYi: 12,
      growthRatePct: 100,
      basePe: 30,
      indexPoints: 4090,
      sentimentCoef: 1,
      actualCapYi: 2083,
    })
    expect(result.impliedSentimentCoef).not.toBeNull()
    expect(result.impliedSentimentCoef ?? 0).toBeGreaterThan(2.05)
    expect(result.impliedSentimentCoef ?? 0).toBeLessThan(2.2)
  })

  it('converts helpers', () => {
    expect(indexPointsToCoef(3300)).toBeCloseTo(1.1, 3)
    expect(growthRateToCoef(30)).toBeCloseTo(1.3, 3)
  })

  it('loads presets', () => {
    expect(VALUATION_PRESETS.length).toBeGreaterThanOrEqual(4)
    const first = presetToFormValues(VALUATION_PRESETS[0])
    expect(first.symbol).toBeTruthy()
  })
})
