import { describe, expect, it } from 'vitest'
import {
  allowsOptionalBacktestExitEvents,
  isEventIndependentBacktestStrategy,
  parseStrategyParamSchema,
} from '@/shared/utils/strategyParams'

describe('strategyParams backtest helpers', () => {
  it('treats trend_king rally as event-independent', () => {
    expect(isEventIndependentBacktestStrategy('trend_king_rally_v1')).toBe(true)
  })

  it('allows optional exit events for trend_king strategies', () => {
    expect(allowsOptionalBacktestExitEvents('trend_king_rally_v1')).toBe(true)
    expect(allowsOptionalBacktestExitEvents('wyckoff_trend_v1')).toBe(false)
  })

  it('uses Chinese parameter labels while preserving the API keys and constraints', () => {
    const parsed = parseStrategyParamSchema({ min_score: {type:'number',title:'Signal score min',minimum:0,maximum:100,default:60}, require_sequence:{type:'boolean',title:'Require event sequence'} })
    expect(parsed.min_score.title).toBe('最低信号评分')
    expect(parsed.min_score.minimum).toBe(0)
    expect(parsed.min_score.defaultValue).toBe(60)
    expect(parsed.require_sequence.title).toBe('要求事件顺序完整')
  })
})
