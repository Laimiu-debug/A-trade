import { describe, expect, it } from 'vitest'
import {denseRiskGrid, gridSummary, numericAxis} from '../shared/utils/plateauGrid'

describe('complete parameter planes',()=>{
  it('generates all 441 risk parameter points without truncating at 16 axis values',()=>{
    const dense=denseRiskGrid(21)
    const stop=numericAxis(dense.stopLoss,{min:0,max:50})
    const take=numericAxis(dense.takeProfit,{min:0,max:150})
    expect(stop).toHaveLength(21); expect(take).toHaveLength(21)
    expect(stop[0]).toBe(3); expect(stop.at(-1)).toBe(13)
    expect(take[0]).toBe(5); expect(take.at(-1)).toBe(35)
    expect(gridSummary({stop,take,fixed:[]})).toMatchObject({total:441,isSurface:true,exceedsLimit:false,dimensions:'21 × 21'})
  })
  it('shows one point when axes are empty and detects excess dimensional products',()=>{
    expect(gridSummary({a:[],b:[]})).toMatchObject({total:1,isSurface:false})
    expect(gridSummary({a:Array.from({length:64},(_,i)=>i),b:Array.from({length:64},(_,i)=>i)})).toMatchObject({total:4096,exceedsLimit:true})
  })
  it('rejects bad values instead of silently changing or dropping requested coordinates',()=>{
    for(const raw of ['-1,2','3,nope','1,90','1,1.5'])expect(()=>numericAxis(raw,{integer:true,min:0,max:50})).toThrow()
    expect(numericAxis('3，2 2;1',{min:0,max:50})).toEqual([1,2,3])
    expect(()=>numericAxis(Array.from({length:65},(_,i)=>i).join(','),{min:0,max:100})).toThrow(/64/)
  })
})
