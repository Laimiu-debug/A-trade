import type { BacktestPlateauPoint, BacktestPlateauResponse } from '@/types/contracts'

export function plateauRecommendation(result: BacktestPlateauResponse | null | undefined): BacktestPlateauPoint | null {
  if (isLegacyRhythmResult(result?.base_payload?.strategy_id, result?.strategy_version)) return null
  return result?.regions?.map(region => region.center_point).find(point =>
    !point.error && point.passes_hard_filters === true && point.stats.total_return > 0,
  ) ?? null
}

export function isLegacyRhythmResult(strategyId: string | undefined, version: string | undefined): boolean {
  if (strategyId !== 'ths_force_rhythm_v1') return false
  const match = /^(\d+)\.(\d+)\.(\d+)/.exec(version ?? '')
  if (!match) return true
  const [major, minor, patch] = match.slice(1).map(Number)
  return major < 1 || major === 1 && minor === 0 && patch < 1
}

export function resolvePlateauAxes(points: BacktestPlateauPoint[], axes: string[], x: string, y: string): [string, string] {
  const varying = axes.filter(axis => new Set(points.filter(point => !point.error).map(point =>
    Number(point.params[axis as keyof typeof point.params]),
  ).filter(Number.isFinite)).size > 1)
  if (varying.length < 2) return [x, y]
  const nextX = varying.includes(x) ? x : varying[0]
  return [nextX, varying.includes(y) && y !== nextX ? y : varying.find(axis => axis !== nextX)!]
}
