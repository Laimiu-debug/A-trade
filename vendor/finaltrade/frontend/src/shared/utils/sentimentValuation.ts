import type {
  ValuationBreakdown,
  ValuationFormValues,
  ValuationPreset,
} from '@/types/contracts'

export type { IndustryPeTier, ValuationBreakdown, ValuationFormValues, ValuationPreset } from '@/types/contracts'

const INDEX_BASE = 3000

export const INDUSTRY_PE_OPTIONS: Array<{ label: string; value: number; tier: import('@/types/contracts').IndustryPeTier }> = [
  { label: '银行/保险 10x', value: 10, tier: 'bank_insurance' },
  { label: '传统行业 12.5x', value: 12.5, tier: 'traditional' },
  { label: '消费 17.5x', value: 17.5, tier: 'consumer' },
  { label: '半科技 22.5x', value: 22.5, tier: 'semi_tech' },
  { label: '科技 27.5x', value: 27.5, tier: 'tech' },
  { label: '高成长稀缺 30x', value: 30, tier: 'high_growth' },
]

export const VALUATION_PRESETS: ValuationPreset[] = [
  {
    key: 'changjiang',
    symbol: 'sh600900',
    name: '长江电力',
    earningsYi: 358,
    growthRatePct: 5,
    basePe: 12.5,
    indexPoints: 4090,
    sentimentCoef: 1.0,
    actualCapYi: 6523,
    note: '稳定蓝筹，情绪溢价接近 1',
  },
  {
    key: 'maotai',
    symbol: 'sh600519',
    name: '贵州茅台',
    earningsYi: 863,
    growthRatePct: -15,
    basePe: 15,
    indexPoints: 4090,
    sentimentCoef: 1.0,
    actualCapYi: 15188,
    note: '定价已含较悲观预期，若增速修正有上行空间',
  },
  {
    key: 'yuanjie',
    symbol: 'sh688498',
    name: '源杰科技',
    earningsYi: 12,
    growthRatePct: 100,
    basePe: 30,
    indexPoints: 4090,
    sentimentCoef: 1.0,
    actualCapYi: 2083,
    note: '稀缺龙头，隐含情绪溢价约 2.1x',
  },
  {
    key: 'honghe',
    symbol: 'sh603256',
    name: '宏和科技',
    earningsYi: 10,
    growthRatePct: 150,
    basePe: 30,
    indexPoints: 4090,
    sentimentCoef: 1.0,
    actualCapYi: 2332,
    note: 'Low-CTE 布龙头，隐含情绪溢价约 2.3x',
  },
  {
    key: 'zhongji',
    symbol: 'sz300308',
    name: '中际旭创',
    earningsYi: 50,
    growthRatePct: 20,
    basePe: 20,
    indexPoints: 4090,
    sentimentCoef: 1.5,
    actualCapYi: 0,
    note: '业绩驱动为主，情绪为辅的典型',
  },
]

export function growthRateToCoef(growthRatePct: number) {
  return 1 + growthRatePct / 100
}

export function indexPointsToCoef(indexPoints: number, basePoints = INDEX_BASE) {
  if (basePoints <= 0) return 1
  return Math.max(0.5, indexPoints / basePoints)
}

export function calcTheoreticalCap(values: ValuationFormValues): ValuationBreakdown {
  const growthCoef = growthRateToCoef(values.growthRatePct)
  const indexCoef = indexPointsToCoef(values.indexPoints)
  const base =
    values.earningsYi * growthCoef * values.basePe * indexCoef
  const theoreticalCapYi = base * values.sentimentCoef
  const impliedSentimentCoef =
    values.actualCapYi != null && values.actualCapYi > 0 && base > 0
      ? values.actualCapYi / base
      : null
  const gapPct =
    values.actualCapYi != null && theoreticalCapYi > 0
      ? ((values.actualCapYi - theoreticalCapYi) / theoreticalCapYi) * 100
      : null

  return {
    theoreticalCapYi,
    earningsComponentYi: values.earningsYi,
    growthCoef,
    basePe: values.basePe,
    indexCoef,
    sentimentCoef: values.sentimentCoef,
    impliedSentimentCoef,
    gapPct,
  }
}

export function formatCapYi(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value.toFixed(1)} 亿`
}

export function formatCoef(value: number | null | undefined, digits = 2) {
  if (value == null || !Number.isFinite(value)) return '--'
  return value.toFixed(digits)
}

export function sentimentLabel(coef: number) {
  if (coef < 0.95) return '偏低'
  if (coef <= 1.15) return '中性'
  if (coef <= 2.0) return '温和溢价'
  if (coef <= 3.5) return '高溢价'
  if (coef <= 6.0) return '极高溢价'
  return '超极值'
}

export function sentimentColor(coef: number) {
  if (coef < 0.95) return '#19744f'
  if (coef <= 1.15) return '#375553'
  if (coef <= 2.0) return '#b86f1c'
  if (coef <= 3.5) return '#c4473d'
  if (coef <= 6.0) return '#9f1239'
  return '#7f1d1d'
}

export function defaultFormValues(): ValuationFormValues {
  return {
    symbol: '',
    name: '',
    earningsYi: 10,
    growthRatePct: 20,
    basePe: 20,
    indexPoints: 4090,
    sentimentCoef: 1.0,
    actualCapYi: null,
  }
}

export function presetToFormValues(preset: ValuationPreset): ValuationFormValues {
  return {
    symbol: preset.symbol,
    name: preset.name,
    earningsYi: preset.earningsYi,
    growthRatePct: preset.growthRatePct,
    basePe: preset.basePe,
    indexPoints: preset.indexPoints,
    sentimentCoef: preset.sentimentCoef,
    actualCapYi: preset.actualCapYi > 0 ? preset.actualCapYi : null,
  }
}

export const FORMULA_TEXT =
  '市值 = 当期盈利 × 复合增速系数 × 基准PE × 大盘水位系数 × 情绪溢价'
