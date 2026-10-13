import type { CandlePoint } from '@/types/contracts'

export type ThsForceState = 'rising' | 'falling' | 'flat'

export interface ThsMainRetailPoint {
  time: string
  mainForce: number
  retailForce: number
  prevMainForce: number
  prevRetailForce: number
  prev2MainForce: number
  mainForceState: ThsForceState
  prevMainForceState: ThsForceState
  purpleToYellow: boolean
  goldenCross: boolean
}

function rollingAverage(values: number[], window: number) {
  const span = Math.max(1, Math.round(window))
  const result: number[] = []
  const queue: number[] = []
  let running = 0

  for (const raw of values) {
    const value = Number(raw) || 0
    queue.push(value)
    running += value
    if (queue.length > span) {
      running -= queue.shift() ?? 0
    }
    result.push(running / Math.max(queue.length, 1))
  }

  return result
}

function exponentialMovingAverage(values: number[], window: number) {
  const span = Math.max(1, Math.round(window))
  const alpha = 2 / (span + 1)
  const result: number[] = []
  let emaValue = 0

  values.forEach((raw, index) => {
    const value = Number(raw) || 0
    if (index === 0) {
      emaValue = value
    } else {
      emaValue = alpha * value + (1 - alpha) * emaValue
    }
    result.push(emaValue)
  })

  return result
}

function roundNumber(value: number, digits = 6) {
  return Number(value.toFixed(digits))
}

function resolveMainForceState(current: number, previous: number): ThsForceState {
  if (current > previous) return 'rising'
  if (current < previous) return 'falling'
  return 'flat'
}

export function calculateThsMainRetailSeries(candles: CandlePoint[]): ThsMainRetailPoint[] {
  if (candles.length <= 0) return []

  const closes = candles.map((item) => Number(item.close) || 0)
  const volumes = candles.map((item) => (Number(item.volume) || 0) / 100)
  const upVolume: number[] = [0]
  const downVolume: number[] = [0]

  for (let index = 1; index < candles.length; index += 1) {
    const volume = volumes[index]
    if (closes[index] > closes[index - 1]) {
      upVolume.push(volume)
      downVolume.push(0)
    } else if (closes[index] < closes[index - 1]) {
      upVolume.push(0)
      downVolume.push(volume)
    } else {
      upVolume.push(0)
      downVolume.push(0)
    }
  }

  const mainForceSeries = exponentialMovingAverage(rollingAverage(upVolume, 3), 3)
  const retailForceSeries = exponentialMovingAverage(rollingAverage(downVolume, 3), 10)

  return candles.map((item, index) => {
    const mainForce = Number(mainForceSeries[index] ?? 0)
    const retailForce = Number(retailForceSeries[index] ?? 0)
    const prevMainForce = index >= 1 ? Number(mainForceSeries[index - 1] ?? mainForce) : mainForce
    const prevRetailForce = index >= 1 ? Number(retailForceSeries[index - 1] ?? retailForce) : retailForce
    const prev2MainForce = index >= 2 ? Number(mainForceSeries[index - 2] ?? prevMainForce) : prevMainForce
    const mainForceState = resolveMainForceState(mainForce, prevMainForce)
    const prevMainForceState = resolveMainForceState(prevMainForce, prev2MainForce)
    const purpleToYellow =
      index >= 2
      && prevMainForce > 0
      && prevMainForceState === 'falling'
      && mainForce > 0
      && mainForceState === 'rising'
    const goldenCross =
      index >= 1
      && mainForce > retailForce
      && prevMainForce <= prevRetailForce

    return {
      time: item.time,
      mainForce: roundNumber(mainForce),
      retailForce: roundNumber(retailForce),
      prevMainForce: roundNumber(prevMainForce),
      prevRetailForce: roundNumber(prevRetailForce),
      prev2MainForce: roundNumber(prev2MainForce),
      mainForceState,
      prevMainForceState,
      purpleToYellow,
      goldenCross,
    }
  })
}
