export type PendingBuySource = 'signals' | 'screener'
export type PendingBuySizingMode = 'lots' | 'amount' | 'position'

export interface PendingBuyDraft {
  id: string
  symbol: string
  name?: string
  source: PendingBuySource
  signal_date: string
  created_at: string
  reference_price?: number
  sizing_mode: PendingBuySizingMode
  sizing_value: number
}

interface UpsertPendingBuyDraftInput {
  symbol: string
  name?: string
  source: PendingBuySource
  signal_date: string
  default_quantity?: number
  reference_price?: number
}

const STORAGE_KEY = 'tdx-sim-pending-buy-orders-v1'
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

function normalizeSizingMode(value: unknown): PendingBuySizingMode {
  return value === 'amount' || value === 'position' ? value : 'lots'
}

function normalizeNumber(value: unknown): number | undefined {
  if (typeof value !== 'number') return undefined
  if (!Number.isFinite(value)) return undefined
  return value
}

function normalizeDraft(raw: unknown): PendingBuyDraft | null {
  if (!raw || typeof raw !== 'object') return null
  const data = raw as Record<string, unknown>
  const symbol = typeof data.symbol === 'string' ? data.symbol.trim().toLowerCase() : ''
  if (!symbol || symbol.length < 4) return null
  const source = data.source === 'screener' ? 'screener' : data.source === 'signals' ? 'signals' : null
  if (!source) return null
  const signalDate = typeof data.signal_date === 'string' ? data.signal_date.trim() : ''
  if (!DATE_PATTERN.test(signalDate)) return null
  const createdAt = typeof data.created_at === 'string' && data.created_at.trim()
    ? data.created_at.trim()
    : new Date().toISOString()
  const sizingMode = normalizeSizingMode(data.sizing_mode)
  const sizingValueRaw = normalizeNumber(data.sizing_value)
  const sizingValue = sizingValueRaw && sizingValueRaw > 0 ? sizingValueRaw : 1
  const id = typeof data.id === 'string' && data.id.trim()
    ? data.id.trim()
    : `${source}:${symbol}:${signalDate}`
  const referencePriceRaw = normalizeNumber(data.reference_price)
  const referencePrice = referencePriceRaw && referencePriceRaw > 0 ? referencePriceRaw : undefined
  const name = typeof data.name === 'string' && data.name.trim() ? data.name.trim() : undefined
  return {
    id,
    symbol,
    name,
    source,
    signal_date: signalDate,
    created_at: createdAt,
    reference_price: referencePrice,
    sizing_mode: sizingMode,
    sizing_value: sizingValue,
  }
}

function safeReadStorage(): PendingBuyDraft[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const normalized = parsed
      .map((item) => normalizeDraft(item))
      .filter((item): item is PendingBuyDraft => item !== null)
    return normalized
  } catch {
    return []
  }
}

export function readPendingBuyDrafts(): PendingBuyDraft[] {
  return safeReadStorage()
}

export function writePendingBuyDrafts(drafts: PendingBuyDraft[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(drafts))
  } catch {
    // ignore localStorage failures
  }
}

export function clearPendingBuyDrafts() {
  writePendingBuyDrafts([])
}

export function upsertPendingBuyDraft(input: UpsertPendingBuyDraftInput) {
  const symbol = input.symbol.trim().toLowerCase()
  const signalDate = input.signal_date.trim()
  if (!symbol || symbol.length < 4 || !DATE_PATTERN.test(signalDate)) {
    throw new Error('INVALID_PENDING_BUY_DRAFT')
  }

  const list = readPendingBuyDrafts()
  const id = `${input.source}:${symbol}:${signalDate}`
  const index = list.findIndex((item) => item.id === id)
  const referencePrice = typeof input.reference_price === 'number' && input.reference_price > 0
    ? input.reference_price
    : undefined
  const defaultLots = Math.max(1, Math.round((input.default_quantity ?? 1000) / 100))

  if (index >= 0) {
    const previous = list[index]
    const updated: PendingBuyDraft = {
      ...previous,
      name: input.name?.trim() || previous.name,
      reference_price: referencePrice ?? previous.reference_price,
    }
    const next = [...list]
    next[index] = updated
    writePendingBuyDrafts(next)
    return { inserted: false, item: updated, items: next }
  }

  const created: PendingBuyDraft = {
    id,
    symbol,
    name: input.name?.trim() || undefined,
    source: input.source,
    signal_date: signalDate,
    created_at: new Date().toISOString(),
    reference_price: referencePrice,
    sizing_mode: 'lots',
    sizing_value: defaultLots,
  }
  const next = [created, ...list]
  writePendingBuyDrafts(next)
  return { inserted: true, item: created, items: next }
}
