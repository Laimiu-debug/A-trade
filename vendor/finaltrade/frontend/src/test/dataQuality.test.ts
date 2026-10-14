import { describe, expect, it } from 'vitest'
import { describeDataQuality } from '@/shared/utils/dataQuality'

describe('data availability explanations', () => {
  it('explains the actual missing field and its screening impact', () => {
    const result = describeDataQuality('PARTIAL_FLOAT_SHARES_MISSING')
    expect(result.title).toBe('部分股票缺少流通股本')
    expect(result.description).toContain('换手率')
    expect(result.description).toContain('重新加载输入池')
    expect(result.type).toBe('warning')
  })
  it('does not call a normal cache hit a data failure or let it hide a real partial scan', () => {
    expect(describeDataQuality('INPUT_POOL_CACHE_HIT').type).toBe('info')
    const result = describeDataQuality('INPUT_POOL_CACHE_HIT;PARTIAL_FLOAT_SHARES_MISSING|INPUT_POOL_LOAD_TIMEOUT_PARTIAL')
    expect(result.type).toBe('warning')
    expect(result.title).toBe('部分股票缺少流通股本')
    expect(result.description).toContain('可能遗漏其他股票')
  })
  it('keeps unknown limitations explicit instead of inventing a missing-field diagnosis', () => {
    expect(describeDataQuality('UNRECOGNIZED_REASON').description).toContain('未确认的数据限制')
    expect(describeDataQuality(null).description).toContain('未提供具体原因')
    expect(describeDataQuality('TREND_POOL_STEP3_EMPTY').title).toBe('所选阶段的股票池为空')
  })
})
