import { describe, expect, it } from 'vitest'
import { resolveSignalPriority } from '@/shared/utils/signals'

describe('resolveSignalPriority', () => {
  it('uses B > A > C ordering', () => {
    const result = resolveSignalPriority(['C', 'A', 'B'])
    expect(result.primary).toBe('B')
    expect(result.secondary).toEqual(['A', 'C'])
  })

  it('returns undefined primary for empty input', () => {
    const result = resolveSignalPriority([])
    expect(result.primary).toBeUndefined()
    expect(result.secondary).toEqual([])
  })
})

