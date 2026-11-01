import { describe, expect, it } from 'vitest'
import { formatBacktestSymbolLimit, restoreBacktestSymbolLimit } from '../shared/utils/backtestUniverse'

describe('full selected pool by default', () => {
  it('does not carry the old implicit 120-stock cap into the new draft', () => {
    expect(restoreBacktestSymbolLimit({ max_symbols: 120 })).toBe(0)
    expect(restoreBacktestSymbolLimit({})).toBe(0)
  })
  it('restores only an explicitly enabled new limit', () => {
    expect(restoreBacktestSymbolLimit({ max_symbols: 300, stock_limit_enabled: true })).toBe(300)
    expect(restoreBacktestSymbolLimit({ max_symbols: 120, stock_limit_enabled: false })).toBe(0)
  })
  it('rejects stale or corrupt counts rather than enabling a hidden limit', () => {
    for (const max_symbols of [null, -1, 0, 4.5, 9999, 'bad']) {
      expect(restoreBacktestSymbolLimit({ max_symbols, stock_limit_enabled: true })).toBe(0)
    }
  })
  it('labels the full pool without displaying an internal zero as a stock count', () => {
    expect(formatBacktestSymbolLimit(0)).toBe('所选股票池全部股票')
    expect(formatBacktestSymbolLimit(120)).toBe('最多 120 只')
  })
})
