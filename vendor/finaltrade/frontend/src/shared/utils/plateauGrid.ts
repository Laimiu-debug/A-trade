export const MAX_PLATEAU_POINTS = 2000
export const MAX_AXIS_VALUES = 64

export function numericAxis(raw: string, options: {integer?: boolean; min:number; max:number; precision?:number; maxLength?:number}): number[] {
  const tokens = String(raw || '').split(/[,\s，、;；]+/).filter(Boolean)
  const result = new Set<number>()
  for (const token of tokens) {
    const number = Number(token)
    if (!Number.isFinite(number) || number < options.min || number > options.max || options.integer && !Number.isInteger(number)) {
      throw new Error(`参数 ${token} 无效，请输入 ${options.min}–${options.max} 范围内的${options.integer ? '整数' : '数值'}`)
    }
    result.add(options.integer ? number : Number(number.toFixed(options.precision ?? 6)))
  }
  if (result.size > (options.maxLength ?? MAX_AXIS_VALUES)) throw new Error(`单个参数最多 ${options.maxLength ?? MAX_AXIS_VALUES} 个取值`)
  return [...result].sort((a,b) => a-b)
}

export function gridSummary(axes: Record<string, number[]>) {
  const varying = Object.entries(axes).filter(([,values]) => values.length > 1)
  const total = Object.values(axes).reduce((count, values) => count * Math.max(1,values.length),1)
  return {total, varying, dimensions:varying.map(([,values])=>values.length).join(' × '), isSurface:varying.length>=2, exceedsLimit:total>MAX_PLATEAU_POINTS}
}

export function denseRiskGrid(size: number) {
  if (![7,11,21].includes(size)) throw new Error('网格密度不支持')
  const range = (min:number, max:number) => Array.from({length:size},(_,i)=>Number((min+(max-min)*i/(size-1)).toFixed(4)))
  return {stopLoss:range(3,13).join(','),takeProfit:range(5,35).join(','),points:size*size}
}
