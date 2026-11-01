/** A zero limit selects the complete pool; limits are always opt-in. */
export function restoreBacktestSymbolLimit(draft: { max_symbols?: unknown; stock_limit_enabled?: unknown }): number {
  const value = Number(draft.max_symbols)
  return draft.stock_limit_enabled === true && Number.isInteger(value) && value > 0 && value <= 2000 ? value : 0
}

export function formatBacktestSymbolLimit(value: number): string {
  return value > 0 ? `最多 ${value} 只` : '所选股票池全部股票'
}
