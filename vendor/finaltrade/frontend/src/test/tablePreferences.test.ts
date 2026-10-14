import { describe, expect, it } from 'vitest'
import { moveTableColumn, normalizeTablePreferences } from '@/shared/utils/tablePreferences'

const definitions = [
  { key: 'symbol', title: '代码', width: 110 },
  { key: 'entry_date', title: '买入日', width: 116, minWidth: 116 },
  { key: 'exit_date', title: '卖出日', width: 116, minWidth: 116 },
]

describe('saved transaction table settings', () => {
  it('retains edits, removes stale and duplicate keys, and appends newly added columns', () => {
    const result = normalizeTablePreferences([
      { key: 'entry_date', title: '买入日期', width: 150, visible: false },
      { key: 'obsolete', title: '旧字段' },
      { key: 'entry_date', title: '重复字段' },
      { key: 'symbol', title: '股票代码', width: 130 },
    ], definitions)
    expect(result).toEqual([
      { key: 'entry_date', title: '买入日期', width: 150, visible: false },
      { key: 'symbol', title: '股票代码', width: 130, visible: true },
      { key: 'exit_date', title: '卖出日', width: 116, visible: true },
    ])
  })
  it('repairs corrupt saved settings without allowing dates to become too narrow or all columns to disappear', () => {
    const result = normalizeTablePreferences([
      { key: 'symbol', title: ' ', width: Number.NaN, visible: false },
      { key: 'entry_date', width: 20, visible: false },
      { key: 'exit_date', width: 900, visible: false },
    ], definitions)
    expect(result.map(column => column.width)).toEqual([110, 116, 600])
    expect(result[0].title).toBe('代码')
    expect(result.map(column => column.visible)).toEqual([true, false, false])
    expect(normalizeTablePreferences({ broken: true }, definitions)).toHaveLength(3)
  })
  it('moves a column without changing its label or visibility', () => {
    const original = normalizeTablePreferences(null, definitions)
    expect(moveTableColumn(original, 'exit_date', -1).map(column => column.key)).toEqual(['symbol', 'exit_date', 'entry_date'])
    expect(original.map(column => column.key)).toEqual(['symbol', 'entry_date', 'exit_date'])
    expect(moveTableColumn(original, 'symbol', -1)).toBe(original)
  })
})
