import type { StrategyDescriptor, StrategyId } from '@/types/contracts'

type StrategyParamType = 'number' | 'integer' | 'boolean' | 'enum'

const PARAMETER_TITLES: Record<string, string> = {
  min_score: '最低信号评分',
  min_event_count: '最少事件数量',
  require_sequence: '要求事件顺序完整',
  health_score_min: '最低健康评分',
  event_score_min: '最低事件评分',
  event_grade_min: '最低事件等级',
  require_key_event_confirmation: '要求关键事件确认',
}

export type StrategyParamSpec = {
  key: string
  title: string
  type: StrategyParamType
  minimum?: number
  maximum?: number
  options: string[]
  defaultValue?: unknown
}

const STRATEGY_ENUM_OPTION_LABELS: Record<string, Record<string, string>> = {
  matrix_event_semantic_version: {
    matrix_v1: '矩阵基础规则',
    aligned_wyckoff_v2: '对齐威科夫V2规则',
  },
  event_grade_min: {
    A: 'A级',
    B: 'B级',
    C: 'C级',
  },
}

const HIDDEN_STRATEGY_IDS = new Set<string>([
  'main_force_crossover_v1',
  'main_force_launch_v1',
  'main_force_pullback_v1',
])

const HIDDEN_STRATEGY_NAME_PREFIXES = [
  '主力量能金叉',
  '主力启动金叉',
  '主力回踩续涨',
]

/** 与 backend EVENT_INDEPENDENT_STRATEGY_IDS 对齐：用策略内置信号入场，不依赖威科夫 entry_events */
const EVENT_INDEPENDENT_BACKTEST_STRATEGY_IDS = new Set<string>([
  'score_only_rank_v1',
  'matrix_signal_v1',
  'ths_main_force_flip_v1',
  'ths_main_force_golden_cross_v1',
  'ths_force_rhythm_v1',
  'emotion_limit_up_v1',
  'limit_up_arb_v1',
  'wulong_cluster_v1',
  'b1_mtf_v1',
  'trend_king_v1',
  'trend_king_limitup_v1',
  'trend_king_rally_v1',
  'trend_king_pullback_v1',
  'relative_strength_breakout_v1',
])

const OPTIONAL_EXIT_EVENT_BACKTEST_STRATEGY_IDS = new Set<string>([
  'ths_main_force_flip_v1',
  'ths_main_force_golden_cross_v1',
  'ths_force_rhythm_v1',
  'emotion_limit_up_v1',
  'limit_up_arb_v1',
  'wulong_cluster_v1',
  'b1_mtf_v1',
  'trend_king_v1',
  'trend_king_limitup_v1',
  'trend_king_rally_v1',
  'trend_king_pullback_v1',
  'relative_strength_breakout_v1',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeStrategyId(raw: unknown, fallback: StrategyId = 'wyckoff_trend_v1'): StrategyId {
  const text = String(raw || '').trim()
  if (HIDDEN_STRATEGY_IDS.has(text)) return fallback
  return text || fallback
}

function isHiddenStrategyName(raw: unknown): boolean {
  const text = String(raw || '').trim()
  if (!text) return false
  return HIDDEN_STRATEGY_NAME_PREFIXES.some((prefix) => text.startsWith(prefix))
}

export function isHiddenStrategy(raw: Pick<StrategyDescriptor, 'strategy_id' | 'name'> | null | undefined): boolean {
  if (!raw) return false
  return HIDDEN_STRATEGY_IDS.has(String(raw.strategy_id || '').trim()) || isHiddenStrategyName(raw.name)
}

export function filterVisibleStrategies(items: StrategyDescriptor[] | null | undefined): StrategyDescriptor[] {
  if (!Array.isArray(items)) return []
  return items.filter((item) => !isHiddenStrategy(item)).map(item => item.strategy_id === 'score_only_rank_v1' ? { ...item, name: '纯评分排名V1' } : item)
}

export function isEventIndependentBacktestStrategy(strategyId: unknown): boolean {
  return EVENT_INDEPENDENT_BACKTEST_STRATEGY_IDS.has(String(strategyId || '').trim())
}

export function allowsOptionalBacktestExitEvents(strategyId: unknown): boolean {
  return OPTIONAL_EXIT_EVENT_BACKTEST_STRATEGY_IDS.has(String(strategyId || '').trim())
}

export function normalizeStrategyParams(raw: unknown): Record<string, unknown> {
  if (!isRecord(raw)) return {}
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(raw)) {
    const normalizedKey = String(key || '').trim()
    if (!normalizedKey) continue
    out[normalizedKey] = value
  }
  return out
}

export function parseStrategyParamSchema(raw: unknown): Record<string, StrategyParamSpec> {
  if (!isRecord(raw)) return {}
  const out: Record<string, StrategyParamSpec> = {}
  for (const [rawKey, rawSpec] of Object.entries(raw)) {
    const key = String(rawKey || '').trim()
    if (!key || !isRecord(rawSpec)) continue
    const typeRaw = String(rawSpec.type || '').trim().toLowerCase()
    const type: StrategyParamType | null =
      typeRaw === 'number' || typeRaw === 'integer' || typeRaw === 'boolean' || typeRaw === 'enum'
        ? (typeRaw as StrategyParamType)
        : null
    if (!type) continue
    const title = PARAMETER_TITLES[key] || String(rawSpec.title || key).trim() || key
    const minimum = Number(rawSpec.minimum)
    const maximum = Number(rawSpec.maximum)
    const options = Array.isArray(rawSpec.options)
      ? rawSpec.options.map((item) => String(item)).filter((item) => item.trim().length > 0)
      : []
    out[key] = {
      key,
      title,
      type,
      minimum: Number.isFinite(minimum) ? minimum : undefined,
      maximum: Number.isFinite(maximum) ? maximum : undefined,
      options,
      defaultValue: rawSpec.default,
    }
  }
  return out
}

export function coerceStrategyParamValue(spec: StrategyParamSpec, raw: unknown): unknown {
  if (spec.type === 'number') {
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return undefined
    let value = parsed
    if (typeof spec.minimum === 'number') value = Math.max(spec.minimum, value)
    if (typeof spec.maximum === 'number') value = Math.min(spec.maximum, value)
    return Number(value)
  }
  if (spec.type === 'integer') {
    const parsed = Number(raw)
    if (!Number.isFinite(parsed)) return undefined
    let value = Math.round(parsed)
    if (typeof spec.minimum === 'number') value = Math.max(Math.round(spec.minimum), value)
    if (typeof spec.maximum === 'number') value = Math.min(Math.round(spec.maximum), value)
    return value
  }
  if (spec.type === 'boolean') {
    if (typeof raw === 'boolean') return raw
    const text = String(raw || '').trim().toLowerCase()
    if (['1', 'true', 'yes', 'on', 'y'].includes(text)) return true
    if (['0', 'false', 'no', 'off', 'n'].includes(text)) return false
    return undefined
  }
  const value = String(raw || '').trim()
  if (!value) return undefined
  if (spec.options.length <= 0) return value
  return spec.options.includes(value) ? value : undefined
}

export function resolveStrategyEnumOptionLabel(specKey: string, rawValue: unknown): string {
  const key = String(specKey || '').trim()
  const value = String(rawValue || '').trim()
  if (!value) return ''
  const mapByKey = STRATEGY_ENUM_OPTION_LABELS[key]
  if (mapByKey && mapByKey[value]) {
    return mapByKey[value]
  }
  return value
}

export function buildStrategyParamsPayload(options: {
  schema: Record<string, StrategyParamSpec>
  params: Record<string, unknown> | null | undefined
  defaults?: Record<string, unknown> | null | undefined
  includeDefaults?: boolean
}): Record<string, unknown> {
  const {
    schema,
    params,
    defaults,
    includeDefaults = false,
  } = options
  const normalizedParams = normalizeStrategyParams(params)
  const normalizedDefaults = normalizeStrategyParams(defaults)
  const source = includeDefaults ? { ...normalizedDefaults, ...normalizedParams } : normalizedParams
  const out: Record<string, unknown> = {}
  for (const [key, spec] of Object.entries(schema)) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) continue
    const coerced = coerceStrategyParamValue(spec, source[key])
    if (coerced === undefined) continue
    out[key] = coerced
  }
  return out
}

export function resolveDefaultStrategyId(
  items: StrategyDescriptor[],
  fallback: StrategyId = 'wyckoff_trend_v1',
): StrategyId {
  const normalized = filterVisibleStrategies(items)
  const enabled = normalized.filter((item) => item.enabled)
  const defaultItem = enabled.find((item) => item.is_default)
  if (defaultItem) return defaultItem.strategy_id
  if (enabled.length > 0) return enabled[0].strategy_id
  const fallbackExists = normalized.some((item) => item.strategy_id === fallback)
  return fallbackExists ? fallback : (normalized[0]?.strategy_id || fallback || 'wyckoff_trend_v1')
}

export type StrategyParamPreset = {
  id: string
  name: string
  saved_at: string
  strategy_id: StrategyId
  strategy_params: Record<string, unknown>
}

type StrategySharedState = {
  last_strategy_id?: StrategyId
  params_by_strategy: Record<string, Record<string, unknown>>
  presets_by_strategy: Record<string, StrategyParamPreset[]>
}

const STRATEGY_SHARED_STORAGE_KEY = 'tdx-strategy-shared-v1'

function buildDefaultSharedState(): StrategySharedState {
  return {
    last_strategy_id: 'wyckoff_trend_v1',
    params_by_strategy: {},
    presets_by_strategy: {},
  }
}

function normalizePreset(raw: unknown): StrategyParamPreset | null {
  if (!isRecord(raw)) return null
  const id = String(raw.id || '').trim()
  const name = String(raw.name || '').trim()
  const savedAt = String(raw.saved_at || '').trim()
  const strategyId = normalizeStrategyId(raw.strategy_id)
  if (!id || !name || !savedAt || HIDDEN_STRATEGY_IDS.has(strategyId)) return null
  return {
    id,
    name,
    saved_at: savedAt,
    strategy_id: strategyId,
    strategy_params: normalizeStrategyParams(raw.strategy_params),
  }
}

function normalizeSharedState(raw: unknown): StrategySharedState {
  const defaults = buildDefaultSharedState()
  if (!isRecord(raw)) return defaults

  const lastStrategyId = normalizeStrategyId(raw.last_strategy_id, defaults.last_strategy_id || 'wyckoff_trend_v1')

  const paramsByStrategy: Record<string, Record<string, unknown>> = {}
  if (isRecord(raw.params_by_strategy)) {
    for (const [key, value] of Object.entries(raw.params_by_strategy)) {
      const strategyId = String(key || '').trim()
      if (!strategyId || HIDDEN_STRATEGY_IDS.has(strategyId)) continue
      paramsByStrategy[strategyId] = normalizeStrategyParams(value)
    }
  }

  const presetsByStrategy: Record<string, StrategyParamPreset[]> = {}
  if (isRecord(raw.presets_by_strategy)) {
    for (const [key, value] of Object.entries(raw.presets_by_strategy)) {
      const strategyId = String(key || '').trim()
      if (!strategyId || HIDDEN_STRATEGY_IDS.has(strategyId) || !Array.isArray(value)) continue
      const normalized = value.map((item) => normalizePreset(item)).filter((item): item is StrategyParamPreset => item !== null)
      presetsByStrategy[strategyId] = normalized.slice(0, 100)
    }
  }

  return {
    last_strategy_id: lastStrategyId,
    params_by_strategy: paramsByStrategy,
    presets_by_strategy: presetsByStrategy,
  }
}

export function loadStrategySharedState(): StrategySharedState {
  if (typeof window === 'undefined') return buildDefaultSharedState()
  try {
    const raw = window.localStorage.getItem(STRATEGY_SHARED_STORAGE_KEY)
    if (!raw) return buildDefaultSharedState()
    return normalizeSharedState(JSON.parse(raw) as unknown)
  } catch {
    return buildDefaultSharedState()
  }
}

export function saveStrategySharedState(state: StrategySharedState): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(
      STRATEGY_SHARED_STORAGE_KEY,
      JSON.stringify(normalizeSharedState(state)),
    )
  } catch {
    // ignore local storage failures
  }
}

export function getSharedStrategyParams(strategyId: StrategyId): Record<string, unknown> {
  const state = loadStrategySharedState()
  return normalizeStrategyParams(state.params_by_strategy[strategyId])
}

export function setSharedStrategyParams(strategyId: StrategyId, params: Record<string, unknown>): void {
  const state = loadStrategySharedState()
  const next: StrategySharedState = {
    ...state,
    last_strategy_id: strategyId,
    params_by_strategy: {
      ...state.params_by_strategy,
      [strategyId]: normalizeStrategyParams(params),
    },
  }
  saveStrategySharedState(next)
}

export function getSharedLastStrategyId(fallback: StrategyId = 'wyckoff_trend_v1'): StrategyId {
  const state = loadStrategySharedState()
  return normalizeStrategyId(state.last_strategy_id, fallback)
}

export function setSharedLastStrategyId(strategyId: StrategyId): void {
  const state = loadStrategySharedState()
  saveStrategySharedState({
    ...state,
    last_strategy_id: normalizeStrategyId(strategyId),
  })
}

export function listSharedStrategyPresets(strategyId: StrategyId): StrategyParamPreset[] {
  const state = loadStrategySharedState()
  const rows = state.presets_by_strategy[strategyId]
  if (!Array.isArray(rows)) return []
  return rows.slice(0, 100)
}

export function saveSharedStrategyPreset(options: {
  strategyId: StrategyId
  name: string
  params: Record<string, unknown>
}): StrategyParamPreset {
  const { strategyId, name, params } = options
  const normalizedName = String(name || '').trim() || 'preset'
  const normalizedParams = normalizeStrategyParams(params)
  const state = loadStrategySharedState()
  const current = listSharedStrategyPresets(strategyId)
  const paramsKey = JSON.stringify(normalizedParams, Object.keys(normalizedParams).sort())
  const existing = current.find((item) => JSON.stringify(item.strategy_params, Object.keys(item.strategy_params).sort()) === paramsKey)
  const nextPreset: StrategyParamPreset = {
    id: existing?.id || `sp_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    name: normalizedName,
    saved_at: new Date().toISOString(),
    strategy_id: strategyId,
    strategy_params: normalizedParams,
  }
  const without = current.filter((item) => item.id !== nextPreset.id)
  const nextRows = [nextPreset, ...without].slice(0, 100)
  saveStrategySharedState({
    ...state,
    last_strategy_id: strategyId,
    params_by_strategy: {
      ...state.params_by_strategy,
      [strategyId]: normalizedParams,
    },
    presets_by_strategy: {
      ...state.presets_by_strategy,
      [strategyId]: nextRows,
    },
  })
  return nextPreset
}

export function deleteSharedStrategyPreset(strategyId: StrategyId, presetId: string): void {
  const state = loadStrategySharedState()
  const rows = listSharedStrategyPresets(strategyId).filter((item) => item.id !== String(presetId || '').trim())
  saveStrategySharedState({
    ...state,
    presets_by_strategy: {
      ...state.presets_by_strategy,
      [strategyId]: rows,
    },
  })
}
