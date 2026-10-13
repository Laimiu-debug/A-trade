import type { CandlePoint } from '@/types/contracts'

export interface CandleRangeStats {
  startDate: string
  endDate: string
  bars: number
  startClose: number
  endClose: number
  change: number
  changePct: number
  highest: number
  highestDate: string
  lowest: number
  lowestDate: string
  amplitudePct: number
  totalVolume: number
  avgVolume: number
  totalAmount: number
  avgAmount: number
  upDays: number
  downDays: number
  flatDays: number
  upRatio: number
  maxDrawdownPct: number
  maxDrawdownDate: string
  maxDailyGainPct: number
  maxDailyGainDate: string
  maxDailyLossPct: number
  maxDailyLossDate: string
}

export function resolveNearestTradingDateIndex(targetDate: string, tradingDates: string[]) {
  if (tradingDates.length === 0) return -1

  const exactIndex = tradingDates.indexOf(targetDate)
  if (exactIndex >= 0) return exactIndex

  const targetTs = Date.parse(targetDate)
  if (Number.isNaN(targetTs)) return -1

  for (let index = 0; index < tradingDates.length; index += 1) {
    const currentTs = Date.parse(tradingDates[index])
    if (Number.isNaN(currentTs)) continue
    if (currentTs >= targetTs) return index
  }

  return tradingDates.length - 1
}

export function computeCandleRangeStats(
  candles: CandlePoint[],
  startDate?: string | null,
  endDate?: string | null,
): CandleRangeStats | null {
  if (candles.length === 0) return null

  const dates = candles.map((item) => item.time)
  const startIndexRaw = startDate ? resolveNearestTradingDateIndex(startDate, dates) : 0
  const endIndexRaw = endDate ? resolveNearestTradingDateIndex(endDate, dates) : candles.length - 1

  if (startIndexRaw < 0 || endIndexRaw < 0) return null

  const left = Math.min(startIndexRaw, endIndexRaw)
  const right = Math.max(startIndexRaw, endIndexRaw)
  const range = candles.slice(left, right + 1)
  if (range.length === 0) return null

  const start = range[0]
  const end = range[range.length - 1]
  const bars = range.length

  let highest = range[0].high
  let highestDate = range[0].time
  let lowest = range[0].low
  let lowestDate = range[0].time
  let totalVolume = 0
  let totalAmount = 0
  let upDays = 0
  let downDays = 0
  let flatDays = 0

  for (const candle of range) {
    totalVolume += candle.volume
    totalAmount += candle.amount

    if (candle.high > highest) {
      highest = candle.high
      highestDate = candle.time
    }
    if (candle.low < lowest) {
      lowest = candle.low
      lowestDate = candle.time
    }

    if (candle.close > candle.open) upDays += 1
    else if (candle.close < candle.open) downDays += 1
    else flatDays += 1
  }

  let peakClose = range[0].close
  let maxDrawdownPct = 0
  let maxDrawdownDate = range[0].time

  for (const candle of range) {
    if (candle.close > peakClose) {
      peakClose = candle.close
      continue
    }
    if (peakClose <= 0) continue
    const drawdown = (peakClose - candle.close) / peakClose
    if (drawdown > maxDrawdownPct) {
      maxDrawdownPct = drawdown
      maxDrawdownDate = candle.time
    }
  }

  let maxDailyGainPct = Number.NEGATIVE_INFINITY
  let maxDailyGainDate = range[0].time
  let maxDailyLossPct = Number.POSITIVE_INFINITY
  let maxDailyLossDate = range[0].time

  for (let i = 1; i < range.length; i += 1) {
    const prevClose = range[i - 1].close
    if (prevClose <= 0) continue
    const ret = (range[i].close - prevClose) / prevClose
    if (ret > maxDailyGainPct) {
      maxDailyGainPct = ret
      maxDailyGainDate = range[i].time
    }
    if (ret < maxDailyLossPct) {
      maxDailyLossPct = ret
      maxDailyLossDate = range[i].time
    }
  }

  if (maxDailyGainPct === Number.NEGATIVE_INFINITY) maxDailyGainPct = 0
  if (maxDailyLossPct === Number.POSITIVE_INFINITY) maxDailyLossPct = 0

  const change = end.close - start.close
  const changePct = start.close > 0 ? change / start.close : 0
  const amplitudePct = lowest > 0 ? (highest - lowest) / lowest : 0
  const avgVolume = totalVolume / bars
  const avgAmount = totalAmount / bars
  const activeBars = upDays + downDays
  const upRatio = activeBars > 0 ? upDays / activeBars : 0

  return {
    startDate: start.time,
    endDate: end.time,
    bars,
    startClose: start.close,
    endClose: end.close,
    change,
    changePct,
    highest,
    highestDate,
    lowest,
    lowestDate,
    amplitudePct,
    totalVolume,
    avgVolume,
    totalAmount,
    avgAmount,
    upDays,
    downDays,
    flatDays,
    upRatio,
    maxDrawdownPct,
    maxDrawdownDate,
    maxDailyGainPct,
    maxDailyGainDate,
    maxDailyLossPct,
    maxDailyLossDate,
  }
}
