export function formatPct(value: number, digits = 2) {
  return `${(value * 100).toFixed(digits)}%`
}

export function formatMoney(value: number) {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 2,
  }).format(value)
}

export function formatLargeMoney(value: number) {
  if (Math.abs(value) >= 100000000) {
    return `${(value / 100000000).toFixed(2)}亿`
  }
  if (Math.abs(value) >= 10000) {
    return `${(value / 10000).toFixed(2)}万`
  }
  return value.toFixed(2)
}

