import { describe, expect, it } from 'vitest'
import type { BacktestPlateauPoint, BacktestPlateauResponse } from '@/types/contracts'
import { isLegacyRhythmResult, plateauRecommendation, resolvePlateauAxes } from '@/shared/utils/plateauSafety'

const point = { params: { window_days: 60, min_score: 55, stop_loss: .04, take_profit: .12 }, passes_hard_filters: true, stats: { total_return: .1 } } as BacktestPlateauPoint
const report = (regions: unknown[], candidate = point) => ({ base_payload: { strategy_id: 'wyckoff_trend_v1' }, regions, recommended_point: candidate, best_point: candidate }) as BacktestPlateauResponse

describe('plateau acceptance boundaries', () => {
  it('does not promote a ranked point when no stable region exists', () => expect(plateauRecommendation(report([]))).toBeNull())
  it('uses only a profitable region center that passed its hard filters', () => {
    expect(plateauRecommendation(report([{ center_point: point }]))).toBe(point)
    expect(plateauRecommendation(report([{ center_point: { ...point, passes_hard_filters: false } }]))).toBeNull()
    expect(plateauRecommendation(report([{ center_point: { ...point, stats: { total_return: -.1 } } }]))).toBeNull()
  })
  it('does not trust legacy rhythm recommendations or label current and future versions as old', () => {
    const legacy = { ...report([{ center_point: point }]), base_payload: { strategy_id: 'ths_force_rhythm_v1' } } as BacktestPlateauResponse
    expect(plateauRecommendation(legacy)).toBeNull()
    expect(isLegacyRhythmResult('ths_force_rhythm_v1', '1.0.0-alpha')).toBe(true)
    expect(isLegacyRhythmResult('ths_force_rhythm_v1', '1.0.1')).toBe(false)
    expect(isLegacyRhythmResult('ths_force_rhythm_v1', '2.0.0')).toBe(false)
  })
  it('selects the scanned stop and take axes while leaving manual valid axes alone', () => {
    const points = [.04, .06].flatMap(stop_loss => [.12, .18].map(take_profit => ({ ...point, params: { ...point.params, stop_loss, take_profit } })))
    const axes = ['window_days', 'min_score', 'stop_loss', 'take_profit']
    expect(resolvePlateauAxes(points, axes, 'window_days', 'min_score')).toEqual(['stop_loss', 'take_profit'])
    expect(resolvePlateauAxes(points, axes, 'take_profit', 'stop_loss')).toEqual(['take_profit', 'stop_loss'])
    expect(resolvePlateauAxes([point], axes, 'window_days', 'min_score')).toEqual(['window_days', 'min_score'])
  })
})
