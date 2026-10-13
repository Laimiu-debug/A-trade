export type StockLibraryItem = {
  ts_code: string
  symbol: string
  name: string
  industry: string
  market: string
  cnspell: string
  exchange: string
  list_status: string
}

export type StockSearchResult = StockLibraryItem & {
  matchType: 'code' | 'name' | 'pinyin'
}

let cache: StockLibraryItem[] | null = null
let loadingPromise: Promise<StockLibraryItem[]> | null = null

function normalizeCode(value: string) {
  return value.trim().toUpperCase()
}

function toPrefixedSymbol(item: StockLibraryItem) {
  const code = item.symbol.trim()
  const exchange = item.exchange.trim().toUpperCase()
  if (!code) return ''
  if (exchange === 'SHSE') return `sh${code}`.toLowerCase()
  if (exchange === 'SZSE') return `sz${code}`.toLowerCase()
  if (exchange === 'BSE') return `bj${code}`.toLowerCase()
  return ''
}

export async function loadStockLibrary() {
  if (cache) return cache
  if (loadingPromise) return loadingPromise

  loadingPromise = (async () => {
    const response = await fetch('/data/stock-database.slim.json')
    if (!response.ok) {
      throw new Error(`Stock library load failed: HTTP_${response.status}`)
    }
    const payload = (await response.json()) as StockLibraryItem[]
    cache = payload.filter((item) => (item.list_status || 'L') === 'L')
    loadingPromise = null
    return cache
  })()

  return loadingPromise
}

export async function searchStockLibrary(query: string, limit = 20) {
  const rows = await loadStockLibrary()
  const keyword = query.trim()
  if (!keyword) return [] as StockSearchResult[]

  const lower = keyword.toLowerCase()
  const upper = normalizeCode(keyword)
  const result: StockSearchResult[] = []
  const seen = new Set<string>()

  const add = (item: StockLibraryItem, matchType: StockSearchResult['matchType']) => {
    if (seen.has(item.ts_code)) return
    seen.add(item.ts_code)
    result.push({ ...item, matchType })
  }

  rows.forEach((item) => {
    const prefixed = toPrefixedSymbol(item)
    if (item.ts_code.toUpperCase() === upper || item.symbol === keyword || prefixed === lower) {
      add(item, 'code')
    }
  })

  rows.forEach((item) => {
    if (item.name === keyword) {
      add(item, 'name')
    }
  })

  rows.forEach((item) => {
    const prefixed = toPrefixedSymbol(item)
    if (
      item.ts_code.toUpperCase().startsWith(upper)
      || item.symbol.startsWith(keyword)
      || (prefixed && prefixed.startsWith(lower))
    ) {
      add(item, 'code')
    }
  })

  rows.forEach((item) => {
    if (item.cnspell.toLowerCase().startsWith(lower)) {
      add(item, 'pinyin')
    }
  })

  rows.forEach((item) => {
    if (item.name.includes(keyword)) {
      add(item, 'name')
    }
  })

  rows.forEach((item) => {
    if (item.cnspell.toLowerCase().includes(lower)) {
      add(item, 'pinyin')
    }
  })

  result.sort((a, b) => {
    const order = { code: 0, name: 1, pinyin: 2 }
    const typeDiff = order[a.matchType] - order[b.matchType]
    if (typeDiff !== 0) return typeDiff
    return a.ts_code.localeCompare(b.ts_code)
  })

  return result.slice(0, Math.max(1, limit))
}

export async function getStockLibraryStats() {
  const rows = await loadStockLibrary()
  const industrySet = new Set(rows.map((item) => item.industry).filter(Boolean))
  const exchangeSet = new Set(rows.map((item) => item.exchange).filter(Boolean))
  return {
    total: rows.length,
    industries: industrySet.size,
    exchanges: exchangeSet.size,
  }
}

