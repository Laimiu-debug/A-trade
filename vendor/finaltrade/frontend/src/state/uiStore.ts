import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AIAnalysisRecord, StockAnnotation } from '@/types/contracts'

interface UIState {
  selectedSymbol?: string
  stockNameMap: Record<string, string>
  annotationDrafts: Record<string, StockAnnotation>
  latestAIBySymbol: Record<string, AIAnalysisRecord>
  setSelectedSymbol: (symbol: string, name?: string) => void
  upsertDraft: (draft: StockAnnotation) => void
  upsertLatestAIRecord: (record: AIAnalysisRecord) => void
  syncLatestAIRecords: (records: AIAnalysisRecord[]) => void
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      selectedSymbol: undefined,
      stockNameMap: {},
      annotationDrafts: {},
      latestAIBySymbol: {},
      setSelectedSymbol: (symbol, name) =>
        set((state) => ({
          selectedSymbol: symbol,
          stockNameMap:
            name && name.trim().length > 0
              ? { ...state.stockNameMap, [symbol]: name.trim() }
              : state.stockNameMap,
        })),
      upsertDraft: (draft) =>
        set((state) => ({
          annotationDrafts: {
            ...state.annotationDrafts,
            [draft.symbol]: draft,
          },
        })),
      upsertLatestAIRecord: (record) =>
        set((state) => ({
          stockNameMap:
            record.name && record.name.trim().length > 0
              ? { ...state.stockNameMap, [record.symbol]: record.name.trim() }
              : state.stockNameMap,
          latestAIBySymbol: {
            ...state.latestAIBySymbol,
            [record.symbol]: record,
          },
        })),
      syncLatestAIRecords: (records) =>
        set((state) => {
          const latestMap: Record<string, AIAnalysisRecord> = {}
          for (const record of records) {
            const current = latestMap[record.symbol]
            if (!current || current.fetched_at < record.fetched_at) {
              latestMap[record.symbol] = record
            }
          }
          const mergedNames = { ...state.stockNameMap }
          for (const item of Object.values(latestMap)) {
            if (item.name && item.name.trim().length > 0) {
              mergedNames[item.symbol] = item.name.trim()
            }
          }
          return {
            stockNameMap: mergedNames,
            latestAIBySymbol: latestMap,
          }
        }),
    }),
    {
      name: 'tdx-trend-ui-store',
      partialize: (state) => ({
        stockNameMap: state.stockNameMap,
        annotationDrafts: state.annotationDrafts,
        latestAIBySymbol: state.latestAIBySymbol,
      }),
    },
  ),
)
