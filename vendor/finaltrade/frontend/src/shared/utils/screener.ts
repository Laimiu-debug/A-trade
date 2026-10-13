import type { ScreenerResult } from '@/types/contracts'

export const SCREENER_STEP1_RANK_END_MIN = 10
export const SCREENER_STEP1_POOL_CAP = 400
export const SCREENER_STEP1_POOL_MIN_WARN = 10

export type ScreenerPathState = '上涨路径中' | '回踩蓄势' | '高位回撤' | '整理观察'

/** Local calendar day key (YYYY-MM-DD) for screener cache invalidation. */
export function getLocalDayKey(date = new Date()): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/** True when ISO timestamp belongs to the current local calendar day. */
export function isScreenerCacheMarketDataFresh(updatedAt: string | undefined | null, now = new Date()): boolean {
  if (!updatedAt || !updatedAt.trim()) return false
  const parsed = new Date(updatedAt)
  if (Number.isNaN(parsed.getTime())) return false
  return getLocalDayKey(parsed) === getLocalDayKey(now)
}

type Step1RankRangeInput = {
  rank_start?: number | null
  top_n?: number | null
}

type ScreenerPathStateInput = Pick<
  ScreenerResult,
  | 'theme_stage'
  | 'stage'
  | 'trend_class'
  | 'retrace20'
  | 'pullback_days'
  | 'pullback_volume_ratio'
  | 'price_vs_ma20'
  | 'ma10_above_ma20_days'
  | 'ma5_above_ma10_days'
  | 'has_blowoff_top'
  | 'has_divergence_5d'
  | 'has_upper_shadow_risk'
>

function clampInteger(value: unknown, fallback: number, min: number, max: number) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  return Math.max(min, Math.min(max, Math.trunc(numeric)))
}

export function normalizeScreenerStep1RankRange(input: Step1RankRangeInput) {
  const rankStart = clampInteger(input.rank_start, 1, 1, 2000)
  const rankEndRaw = clampInteger(input.top_n, 500, 1, 2000)
  return {
    rank_start: rankStart,
    top_n: Math.max(rankStart, rankEndRaw),
  }
}

export function formatScreenerRankRange(input: Step1RankRangeInput) {
  const normalized = normalizeScreenerStep1RankRange(input)
  if (normalized.rank_start <= 1) {
    return `Top${normalized.top_n}`
  }
  return `第${normalized.rank_start}-${normalized.top_n}名`
}

export function sliceRowsByScreenerRankRange<T>(rows: T[], input: Step1RankRangeInput) {
  const normalized = normalizeScreenerStep1RankRange(input)
  return rows.slice(normalized.rank_start - 1, normalized.top_n)
}

export function getScreenerPathState(row: ScreenerPathStateInput): ScreenerPathState {
  const hasOverheatRisk = row.has_blowoff_top || row.has_divergence_5d || row.has_upper_shadow_risk
  const trendAligned =
    row.price_vs_ma20 >= 0
    && row.ma10_above_ma20_days >= 5
    && row.ma5_above_ma10_days >= 3

  if (
    row.theme_stage === '退潮'
    || (hasOverheatRisk && row.retrace20 >= 0.06)
    || (row.stage === 'Late' && row.retrace20 >= 0.12 && row.pullback_days >= 3)
    || row.price_vs_ma20 <= -0.03
  ) {
    return '高位回撤'
  }

  if (
    trendAligned
    && !hasOverheatRisk
    && row.retrace20 <= 0.08
    && row.pullback_days <= 2
    && row.pullback_volume_ratio <= 0.95
  ) {
    return '上涨路径中'
  }

  if (
    trendAligned
    && !hasOverheatRisk
    && row.retrace20 <= 0.18
    && row.pullback_days <= 8
    && row.pullback_volume_ratio <= 1.05
  ) {
    return '回踩蓄势'
  }

  if (row.retrace20 >= 0.15 || (row.stage === 'Late' && row.pullback_days >= 4)) {
    return '高位回撤'
  }

  return '整理观察'
}
