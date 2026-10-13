import type { CandlePoint } from '@/types/contracts'

export function movingAverage(candles: CandlePoint[], window: number) {
  const result: (number | '-')[] = []
  for (let i = 0; i < candles.length; i += 1) {
    if (i < window - 1) {
      result.push('-')
      continue
    }
    let sum = 0
    for (let j = 0; j < window; j += 1) {
      sum += candles[i - j].close
    }
    result.push(Number((sum / window).toFixed(2)))
  }
  return result
}

