import { describe, expect, it } from 'vitest'
import {
  allowsOptionalBacktestExitEvents,
  isEventIndependentBacktestStrategy,
} from '@/shared/utils/strategyParams'

describe('strategyParams backtest helpers', () => {
  it('treats trend_king rally as event-independent', () => {
    expect(isEventIndependentBacktestStrategy('trend_king_rally_v1')).toBe(true)
  })

  it('allows optional exit events for trend_king strategies', () => {
    expect(allowsOptionalBacktestExitEvents('trend_king_rally_v1')).toBe(true)
    expect(allowsOptionalBacktestExitEvents('wyckoff_trend_v1')).toBe(false)
  })
})
