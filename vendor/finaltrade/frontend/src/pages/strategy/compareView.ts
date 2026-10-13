import type { StrategyId } from '@/types/contracts'

export type CompareParamCategory = 'all' | 'scoring' | 'event' | 'gate' | 'execution' | 'risk' | 'other'

export const COMPARE_PARAM_CATEGORY_LABELS: Record<CompareParamCategory, string> = {
  all: '全部',
  scoring: '评分',
  event: '事件',
  gate: '门控',
  execution: '执行',
  risk: '风险',
  other: '其他',
}

const COMPARE_PARAM_KEY_LABELS: Record<string, string> = {
  matrix_event_semantic_version: '矩阵语义版本',
  rank_weight_health: '健康分权重',
  rank_weight_event: '事件分权重',
  rank_weight_strength: '强势权重',
  rank_weight_volume: '量能权重',
  rank_weight_structure: '结构权重',
  health_score_min: '健康分下限',
  event_score_min: '事件分下限',
  event_grade_min: '事件等级下限',
  require_key_event_confirmation: '关键事件确认必需',
  min_score: '入场质量分下限',
  min_event_count: '最少事件数',
  require_sequence: '要求事件序列',
  min_ret40: '40日涨幅下限',
  max_retrace20: '20日回撤上限',
  min_up_down_volume_ratio: '上下跌量比下限',
  min_vol_slope20: '20日量能斜率下限',
  min_ai_confidence: 'AI置信度下限',
  entry_delay_days: '延迟入场天数',
  delay_invalidation_enabled: '延迟窗口失效保护',
  signal_age_min: '信号年龄最小(天)',
  signal_age_max: '信号年龄最大(天)',
  window_days: '信号窗口天数',
  max_symbols: '最大股票数',
  position_pct: '单笔仓位占比',
  max_positions: '最大并发持仓',
  stop_loss: '止损比例',
  take_profit: '止盈比例',
  priority_topk_per_day: '同日优先TopK',
}

export type CompareViewSnapshot = {
  strategy_ids: StrategyId[]
  only_differences: boolean
  category: CompareParamCategory
}

export type CompareViewPreset = CompareViewSnapshot & {
  id: string
  name: string
  saved_at: string
}

const COMPARE_VIEW_STORAGE_KEY = 'tdx-strategy-compare-view-v1'
const COMPARE_VIEW_PRESETS_STORAGE_KEY = 'tdx-strategy-compare-view-presets-v1'
export const COMPARE_VIEW_PRESETS_MAX_COUNT = 50
export const COMPARE_VIEW_PRESETS_EXPORT_SCHEMA_VERSION = 'strategy_compare_view_presets.v1'
const COMPARE_VIEW_SHARE_PREFIX = 'ftcv1.'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasCjk(text: string): boolean {
  return /[\u3400-\u9FFF]/.test(text)
}

function normalizeCompareParamCategory(raw: unknown): CompareParamCategory {
  const text = String(raw || '').trim()
  if (
    text === 'all'
    || text === 'scoring'
    || text === 'event'
    || text === 'gate'
    || text === 'execution'
    || text === 'risk'
    || text === 'other'
  ) {
    return text
  }
  return 'all'
}

function normalizeCompareStrategyIds(raw: unknown, fallbackStrategyId: StrategyId): StrategyId[] {
  const fallback = String(fallbackStrategyId || '').trim() || 'wyckoff_trend_v1'
  const normalized = Array.isArray(raw)
    ? raw.map((item) => String(item).trim()).filter((item) => item.length > 0)
    : []
  const deduplicated = Array.from(new Set(normalized))
  if (!deduplicated.includes(fallback)) {
    deduplicated.unshift(fallback)
  }
  return deduplicated.slice(0, 4)
}

export function normalizeCompareViewSnapshot(raw: unknown, fallbackStrategyId: StrategyId): CompareViewSnapshot {
  const source = isRecord(raw) ? raw : {}
  return {
    strategy_ids: normalizeCompareStrategyIds(source.strategy_ids, fallbackStrategyId),
    only_differences: typeof source.only_differences === 'boolean' ? source.only_differences : true,
    category: normalizeCompareParamCategory(source.category),
  }
}

export function loadCompareViewSnapshot(fallbackStrategyId: StrategyId): CompareViewSnapshot | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(COMPARE_VIEW_STORAGE_KEY)
    if (!raw) return null
    return normalizeCompareViewSnapshot(JSON.parse(raw) as unknown, fallbackStrategyId)
  } catch {
    return null
  }
}

export function saveCompareViewSnapshot(snapshot: CompareViewSnapshot, fallbackStrategyId: StrategyId): void {
  if (typeof window === 'undefined') return
  try {
    const normalized = normalizeCompareViewSnapshot(snapshot, fallbackStrategyId)
    window.localStorage.setItem(COMPARE_VIEW_STORAGE_KEY, JSON.stringify(normalized))
  } catch {
    // ignore local storage failures
  }
}

function normalizeCompareViewPreset(raw: unknown, fallbackStrategyId: StrategyId): CompareViewPreset | null {
  if (!isRecord(raw)) return null
  const id = String(raw.id || '').trim()
  const name = String(raw.name || '').trim()
  const savedAt = String(raw.saved_at || '').trim()
  if (!id || !name || !savedAt) return null
  const snapshot = normalizeCompareViewSnapshot(raw, fallbackStrategyId)
  return {
    id,
    name,
    saved_at: savedAt,
    ...snapshot,
  }
}

export function loadCompareViewPresets(fallbackStrategyId: StrategyId): CompareViewPreset[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(COMPARE_VIEW_PRESETS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed
      .map((item) => normalizeCompareViewPreset(item, fallbackStrategyId))
      .filter((item): item is CompareViewPreset => item !== null)
      .slice(0, COMPARE_VIEW_PRESETS_MAX_COUNT)
  } catch {
    return []
  }
}

export function saveCompareViewPresets(presets: CompareViewPreset[], fallbackStrategyId: StrategyId): void {
  if (typeof window === 'undefined') return
  try {
    const normalized = presets
      .map((item) => normalizeCompareViewPreset(item, fallbackStrategyId))
      .filter((item): item is CompareViewPreset => item !== null)
      .slice(0, COMPARE_VIEW_PRESETS_MAX_COUNT)
    window.localStorage.setItem(COMPARE_VIEW_PRESETS_STORAGE_KEY, JSON.stringify(normalized))
  } catch {
    // ignore local storage failures
  }
}

export function snapshotIdentity(snapshot: CompareViewSnapshot): string {
  return JSON.stringify({
    strategy_ids: snapshot.strategy_ids,
    only_differences: snapshot.only_differences,
    category: snapshot.category,
  })
}

export function saveCompareViewPreset(options: {
  fallbackStrategyId: StrategyId
  name: string
  snapshot: CompareViewSnapshot
}): CompareViewPreset {
  const { fallbackStrategyId, name, snapshot } = options
  const normalizedSnapshot = normalizeCompareViewSnapshot(snapshot, fallbackStrategyId)
  const normalizedName = String(name || '').trim() || 'compare-view'
  const current = loadCompareViewPresets(fallbackStrategyId)
  const key = snapshotIdentity(normalizedSnapshot)
  const existing = current.find((item) => snapshotIdentity(item) === key)
  const nextPreset: CompareViewPreset = {
    id: existing?.id || `cv_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    name: normalizedName,
    saved_at: new Date().toISOString(),
    ...normalizedSnapshot,
  }
  const rows = [nextPreset, ...current.filter((item) => item.id !== nextPreset.id)].slice(0, COMPARE_VIEW_PRESETS_MAX_COUNT)
  saveCompareViewPresets(rows, fallbackStrategyId)
  return nextPreset
}

export function deleteCompareViewPreset(presetId: string, fallbackStrategyId: StrategyId): void {
  const normalizedId = String(presetId || '').trim()
  if (!normalizedId) return
  const rows = loadCompareViewPresets(fallbackStrategyId).filter((item) => item.id !== normalizedId)
  saveCompareViewPresets(rows, fallbackStrategyId)
}

export function normalizeImportedCompareViewPresets(raw: unknown, fallbackStrategyId: StrategyId): CompareViewPreset[] {
  const payloadRows = Array.isArray(raw)
    ? raw
    : isRecord(raw) && Array.isArray(raw.presets)
      ? raw.presets
      : []
  const nowIso = new Date().toISOString()
  return payloadRows
    .map((item, index) => {
      if (!isRecord(item)) return null
      const snapshot = normalizeCompareViewSnapshot(item, fallbackStrategyId)
      const name = String(item.name || '').trim() || `imported-view-${index + 1}`
      const savedAt = String(item.saved_at || '').trim() || nowIso
      const idRaw = String(item.id || '').trim()
      const id = idRaw || `cv_${Date.now().toString(36)}_${index.toString(36)}_${Math.random().toString(36).slice(2, 6)}`
      return {
        id,
        name,
        saved_at: savedAt,
        ...snapshot,
      } as CompareViewPreset
    })
    .filter((item): item is CompareViewPreset => item !== null)
}

function encodeUtf8ToBase64Url(text: string): string {
  const bytes = new TextEncoder().encode(text)
  let binary = ''
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte)
  })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
}

function decodeUtf8FromBase64Url(raw: string): string {
  const normalized = String(raw || '').trim().replace(/-/g, '+').replace(/_/g, '/')
  const padLength = (4 - (normalized.length % 4)) % 4
  const padded = normalized + '='.repeat(padLength)
  const binary = atob(padded)
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
  return new TextDecoder().decode(bytes)
}

export function buildCompareViewShareCode(snapshot: CompareViewSnapshot, fallbackStrategyId: StrategyId): string {
  const payload = {
    schema_version: 'compare_view_share.v1',
    snapshot: normalizeCompareViewSnapshot(snapshot, fallbackStrategyId),
  }
  return `${COMPARE_VIEW_SHARE_PREFIX}${encodeUtf8ToBase64Url(JSON.stringify(payload))}`
}

export function parseCompareViewShareCode(raw: string, fallbackStrategyId: StrategyId): CompareViewSnapshot | null {
  const text = String(raw || '').trim()
  if (!text) return null
  try {
    const payloadText = text.startsWith(COMPARE_VIEW_SHARE_PREFIX)
      ? text.slice(COMPARE_VIEW_SHARE_PREFIX.length)
      : text
    const decoded = decodeUtf8FromBase64Url(payloadText)
    const parsed = JSON.parse(decoded) as unknown
    if (isRecord(parsed)) {
      if (isRecord(parsed.snapshot)) {
        return normalizeCompareViewSnapshot(parsed.snapshot, fallbackStrategyId)
      }
      return normalizeCompareViewSnapshot(parsed, fallbackStrategyId)
    }
    return null
  } catch {
    return null
  }
}

export function resolveCompareParamCategory(key: string): Exclude<CompareParamCategory, 'all'> {
  const normalized = String(key || '').trim().toLowerCase()
  if (!normalized) return 'other'

  if (
    normalized.includes('event')
    || normalized.includes('matrix')
    || normalized.includes('phase')
    || normalized.includes('sequence')
  ) {
    return 'event'
  }
  if (
    normalized.includes('score')
    || normalized.includes('weight')
    || normalized.includes('confidence')
    || normalized.includes('quality')
    || normalized.includes('grade')
  ) {
    return 'scoring'
  }
  if (
    normalized.includes('entry')
    || normalized.includes('delay')
    || normalized.includes('window')
    || normalized.includes('days')
    || normalized.includes('position')
    || normalized.includes('symbols')
    || normalized.includes('topk')
  ) {
    return 'execution'
  }
  if (
    normalized.includes('risk')
    || normalized.includes('stop')
    || normalized.includes('drawdown')
    || normalized.includes('retrace')
    || normalized.includes('ratio')
    || normalized.includes('volatility')
    || normalized.includes('slope')
  ) {
    return 'risk'
  }
  if (
    normalized.startsWith('require_')
    || normalized.includes('_enabled')
    || normalized.startsWith('min_')
    || normalized.startsWith('max_')
  ) {
    return 'gate'
  }
  return 'other'
}

export function resolveCompareParamLabel(key: string, schemaTitle?: string): string {
  const normalizedKey = String(key || '').trim()
  if (!normalizedKey) return '-'

  const mapped = COMPARE_PARAM_KEY_LABELS[normalizedKey]
  if (mapped) return mapped

  const normalizedTitle = String(schemaTitle || '').trim()
  if (normalizedTitle) {
    if (hasCjk(normalizedTitle)) return normalizedTitle
    if (normalizedTitle !== normalizedKey) return normalizedTitle
  }

  return normalizedKey
}

export function formatCompareParamValue(value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '-'
    return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/\.?0+$/, '')
  }
  const text = String(value).trim()
  return text || '-'
}

