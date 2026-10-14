import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './StrategyCenterPage.css'
import dayjs from 'dayjs'
import { useQuery } from '@tanstack/react-query'
import { Alert, App as AntdApp, Button, Card, Col, Divider, Input, InputNumber, Row, Select, Space, Switch, Tag, Typography } from 'antd'
import { Link, useSearchParams } from 'react-router-dom'
import { getStrategies } from '@/shared/api/endpoints'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { buildPageTitle } from '@/state/aiAssistantStore'
import { DismissibleAlert } from '@/shared/components/DismissibleAlert'
import { PageHeader } from '@/shared/components/PageHeader'
import {
  buildStrategyParamsPayload,
  deleteSharedStrategyPreset,
  filterVisibleStrategies,
  getSharedLastStrategyId,
  getSharedStrategyParams,
  listSharedStrategyPresets,
  normalizeStrategyParams,
  parseStrategyParamSchema,
  resolveStrategyEnumOptionLabel,
  resolveDefaultStrategyId,
  saveSharedStrategyPreset,
  setSharedStrategyParams,
} from '@/shared/utils/strategyParams'
import type { StrategyParamPreset, StrategyParamSpec } from '@/shared/utils/strategyParams'
import type { StrategyId } from '@/types/contracts'
import {
  COMPARE_PARAM_CATEGORY_LABELS,
  COMPARE_VIEW_PRESETS_EXPORT_SCHEMA_VERSION,
  COMPARE_VIEW_PRESETS_MAX_COUNT,
  buildCompareViewShareCode,
  deleteCompareViewPreset,
  formatCompareParamValue as formatParamValue,
  loadCompareViewPresets,
  loadCompareViewSnapshot,
  normalizeCompareViewSnapshot,
  normalizeImportedCompareViewPresets,
  parseCompareViewShareCode,
  resolveCompareParamCategory,
  resolveCompareParamLabel,
  saveCompareViewPreset,
  saveCompareViewPresets,
  saveCompareViewSnapshot,
  snapshotIdentity,
} from './compareView'
import type { CompareParamCategory, CompareViewPreset, CompareViewSnapshot } from './compareView'

export function StrategyCenterPage() {
  const { message } = AntdApp.useApp()
  const [searchParams, setSearchParams] = useSearchParams()
  const compareCodeFromQuery = (searchParams.get('cv') ?? '').trim()
  const appliedCompareCodeFromQueryRef = useRef('')
  const syncCompareCodeToQuery = useCallback((raw: string | null) => {
    const code = String(raw || '').trim()
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous)
      if (code) {
        next.set('cv', code)
      } else {
        next.delete('cv')
      }
      if (next.toString() === previous.toString()) return previous
      return next
    }, { replace: true })
  }, [setSearchParams])

  const initialStrategyId = useMemo<StrategyId>(() => getSharedLastStrategyId('wyckoff_trend_v1'), [])
  const initialCompareSnapshot = useMemo<CompareViewSnapshot>(
    () =>
      loadCompareViewSnapshot(initialStrategyId) ?? {
        strategy_ids: [initialStrategyId],
        only_differences: true,
        category: 'all',
      },
    [initialStrategyId],
  )

  const [strategyId, setStrategyId] = useState<StrategyId>(initialStrategyId)
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>(() => getSharedStrategyParams(initialStrategyId))
  const [compareStrategyIds, setCompareStrategyIds] = useState<StrategyId[]>(initialCompareSnapshot.strategy_ids)
  const [compareOnlyDifferences, setCompareOnlyDifferences] = useState(initialCompareSnapshot.only_differences)
  const [compareCategoryFilter, setCompareCategoryFilter] = useState<CompareParamCategory>(initialCompareSnapshot.category)
  const [comparePresetName, setComparePresetName] = useState('')
  const [comparePresetId, setComparePresetId] = useState<string | null>(null)
  const [comparePresetRefreshTick, setComparePresetRefreshTick] = useState(0)
  const [compareShareCode, setCompareShareCode] = useState('')
  const [strategyPresetName, setStrategyPresetName] = useState('')
  const [strategyPresetId, setStrategyPresetId] = useState<string | null>(null)
  const [strategyPresetRefreshTick, setStrategyPresetRefreshTick] = useState(0)
  const comparePresetImportInputRef = useRef<HTMLInputElement | null>(null)

  const strategyCatalogQuery = useQuery({
    queryKey: ['strategy-catalog'],
    queryFn: getStrategies,
    staleTime: 5 * 60_000,
  })
  const strategyItems = useMemo(
    () => filterVisibleStrategies(strategyCatalogQuery.data?.items),
    [strategyCatalogQuery.data?.items],
  )
  const selectedStrategy = useMemo(
    () => strategyItems.find((item) => item.strategy_id === strategyId) ?? null,
    [strategyId, strategyItems],
  )
  const strategySupportsMatrix = selectedStrategy?.capabilities?.supports_matrix !== false
  const strategySupportsSignalAgeFilter = selectedStrategy?.capabilities?.supports_signal_age_filter !== false
  const strategySupportsEntryDelay = selectedStrategy?.capabilities?.supports_entry_delay !== false
  const strategyParamsSchema = useMemo(
    () => parseStrategyParamSchema(selectedStrategy?.strategy_params_schema ?? {}),
    [selectedStrategy?.strategy_params_schema],
  )
  const strategyParamsDefaults = useMemo(
    () => normalizeStrategyParams(selectedStrategy?.strategy_params_defaults),
    [selectedStrategy?.strategy_params_defaults],
  )
  const strategyParamsPayload = useMemo(
    () => buildStrategyParamsPayload({
      schema: strategyParamsSchema,
      params: strategyParams,
      defaults: strategyParamsDefaults,
      includeDefaults: true,
    }),
    [strategyParams, strategyParamsDefaults, strategyParamsSchema],
  )
  const strategyParamEntries = useMemo<StrategyParamSpec[]>(
    () => Object.values(strategyParamsSchema),
    [strategyParamsSchema],
  )

  useEffect(() => {
    if (strategyItems.length <= 0) return
    const allIds = new Set(strategyItems.map((item) => item.strategy_id))
    if (allIds.has(strategyId)) return
    const fallback = resolveDefaultStrategyId(strategyItems, 'wyckoff_trend_v1')
    setStrategyId(fallback)
    setStrategyParams(getSharedStrategyParams(fallback))
  }, [strategyId, strategyItems])

  useEffect(() => {
    if (!selectedStrategy) return
    setStrategyParams((previous) => {
      const normalizedPrevious = normalizeStrategyParams(previous)
      const merged = {
        ...strategyParamsDefaults,
        ...normalizedPrevious,
      }
      const next = buildStrategyParamsPayload({
        schema: strategyParamsSchema,
        params: merged,
        defaults: strategyParamsDefaults,
        includeDefaults: true,
      })
      return JSON.stringify(next) === JSON.stringify(normalizedPrevious) ? previous : next
    })
  }, [selectedStrategy?.strategy_id, strategyParamsDefaults, strategyParamsSchema])

  useEffect(() => {
    setSharedStrategyParams(strategyId, strategyParamsPayload)
  }, [strategyId, strategyParamsPayload])

  const pageAIContext = useMemo(
    () => ({
      page: 'strategy' as const,
      title: buildPageTitle('strategy', selectedStrategy?.name ?? strategyId),
      refs: { strategy_id: strategyId },
      payload: {
        strategy_id: strategyId,
        strategy_params: strategyParamsPayload,
        strategy_ids: compareStrategyIds,
      },
    }),
    [strategyId, selectedStrategy?.name, strategyParamsPayload, compareStrategyIds],
  )
  useRegisterPageAIContext(pageAIContext)

  useEffect(() => {
    setCompareStrategyIds((previous) => {
      if (previous.includes(strategyId)) return previous
      return [strategyId, ...previous].slice(0, 4)
    })
  }, [strategyId])

  useEffect(() => {
    if (strategyItems.length <= 0) return
    const validIds = new Set(strategyItems.map((item) => item.strategy_id))
    setCompareStrategyIds((previous) => {
      const normalized = previous.filter((item) => validIds.has(item))
      if (normalized.includes(strategyId)) {
        return normalized.length > 0 ? normalized : [strategyId]
      }
      return [strategyId, ...normalized].slice(0, 4)
    })
  }, [strategyId, strategyItems])

  const strategyPresets = useMemo<StrategyParamPreset[]>(
    () => listSharedStrategyPresets(strategyId),
    [strategyId, strategyPresetRefreshTick],
  )
  const selectedStrategyPreset = useMemo(
    () => strategyPresets.find((item) => item.id === strategyPresetId) ?? null,
    [strategyPresetId, strategyPresets],
  )
  const comparePresets = useMemo<CompareViewPreset[]>(
    () => loadCompareViewPresets(strategyId),
    [strategyId, comparePresetRefreshTick],
  )
  const selectedComparePreset = useMemo(
    () => comparePresets.find((item) => item.id === comparePresetId) ?? null,
    [comparePresetId, comparePresets],
  )
  const comparisonItems = useMemo(
    () =>
      compareStrategyIds
        .map((id) => strategyItems.find((item) => item.strategy_id === id))
        .filter((item): item is NonNullable<typeof item> => item !== undefined)
        .map((item) => {
          const schema = parseStrategyParamSchema(item.strategy_params_schema ?? {})
          const defaults = normalizeStrategyParams(item.strategy_params_defaults)
          const shared = getSharedStrategyParams(item.strategy_id)
          const payload = buildStrategyParamsPayload({
            schema,
            params: shared,
            defaults,
            includeDefaults: true,
          })
          return {
            descriptor: item,
            schemaEntries: Object.values(schema),
            payload,
          }
        }),
    [compareStrategyIds, strategyItems, strategyParamsPayload],
  )
  const comparisonKeyOrder = useMemo(() => {
    const keys: string[] = []
    const seen = new Set<string>()
    comparisonItems.forEach((item) => {
      item.schemaEntries.forEach((spec) => {
        if (seen.has(spec.key)) return
        seen.add(spec.key)
        keys.push(spec.key)
      })
      Object.keys(item.payload).forEach((key) => {
        if (seen.has(key)) return
        seen.add(key)
        keys.push(key)
      })
    })
    return keys
  }, [comparisonItems])
  const comparisonKeyTitleMap = useMemo(() => {
    const out: Record<string, string> = {}
    comparisonItems.forEach((item) => {
      item.schemaEntries.forEach((spec) => {
        const key = String(spec.key || '').trim()
        const title = String(spec.title || '').trim()
        if (!key || !title) return
        if (!out[key]) {
          out[key] = title
        }
      })
    })
    return out
  }, [comparisonItems])
  const comparisonDiffMap = useMemo(() => {
    const out: Record<string, boolean> = {}
    comparisonKeyOrder.forEach((key) => {
      const values = comparisonItems.map((item) => formatParamValue(item.payload[key]))
      out[key] = new Set(values).size > 1
    })
    return out
  }, [comparisonItems, comparisonKeyOrder])
  const comparisonDiffCount = useMemo(
    () => comparisonKeyOrder.filter((key) => comparisonDiffMap[key]).length,
    [comparisonDiffMap, comparisonKeyOrder],
  )
  const comparisonKeyCategoryMap = useMemo(() => {
    const out: Record<string, Exclude<CompareParamCategory, 'all'>> = {}
    comparisonKeyOrder.forEach((key) => {
      out[key] = resolveCompareParamCategory(key)
    })
    return out
  }, [comparisonKeyOrder])
  const comparisonCategoryCounts = useMemo(() => {
    const out: Record<CompareParamCategory, number> = {
      all: comparisonKeyOrder.length,
      scoring: 0,
      event: 0,
      gate: 0,
      execution: 0,
      risk: 0,
      other: 0,
    }
    comparisonKeyOrder.forEach((key) => {
      const category = comparisonKeyCategoryMap[key]
      out[category] += 1
    })
    return out
  }, [comparisonKeyCategoryMap, comparisonKeyOrder])
  const comparisonVisibleKeys = useMemo(
    () =>
      comparisonKeyOrder.filter((key) => {
        if (compareOnlyDifferences && !comparisonDiffMap[key]) return false
        if (compareCategoryFilter === 'all') return true
        return comparisonKeyCategoryMap[key] === compareCategoryFilter
      }),
    [compareCategoryFilter, comparisonDiffMap, compareOnlyDifferences, comparisonKeyCategoryMap, comparisonKeyOrder],
  )

  useEffect(() => {
    saveCompareViewSnapshot(
      {
        strategy_ids: compareStrategyIds,
        only_differences: compareOnlyDifferences,
        category: compareCategoryFilter,
      },
      strategyId,
    )
  }, [compareCategoryFilter, compareOnlyDifferences, compareStrategyIds, strategyId])

  useEffect(() => {
    if (!strategyPresetId) return
    if (strategyPresets.some((item) => item.id === strategyPresetId)) return
    setStrategyPresetId(null)
  }, [strategyPresetId, strategyPresets])

  useEffect(() => {
    if (!comparePresetId) return
    if (comparePresets.some((item) => item.id === comparePresetId)) return
    setComparePresetId(null)
  }, [comparePresetId, comparePresets])

  useEffect(() => {
    if (!compareCodeFromQuery) {
      appliedCompareCodeFromQueryRef.current = ''
      return
    }
    if (appliedCompareCodeFromQueryRef.current === compareCodeFromQuery) return
    // applyShareCode 是函数声明(hoisted),运行时合法;React Compiler 的 immutability 检查过于保守
    // eslint-disable-next-line react-hooks/immutability
    const applied = applyShareCode(compareCodeFromQuery)
    if (!applied) {
      appliedCompareCodeFromQueryRef.current = compareCodeFromQuery
      setCompareShareCode('')
      syncCompareCodeToQuery(null)
      message.warning('链接中的对比视图分享参数无效，已自动清理。')
      return
    }
    appliedCompareCodeFromQueryRef.current = compareCodeFromQuery
    setCompareShareCode(compareCodeFromQuery)
  }, [compareCodeFromQuery, message, strategyId, syncCompareCodeToQuery])

  function updateStrategyParam(key: string, value: unknown) {
    const normalizedKey = String(key || '').trim()
    if (!normalizedKey) return
    setStrategyParams((previous) => {
      const next = { ...normalizeStrategyParams(previous) }
      if (value === null || value === undefined || value === '') {
        delete next[normalizedKey]
      } else {
        next[normalizedKey] = value
      }
      return next
    })
  }

  function handleSaveStrategyPreset() {
    const preset = saveSharedStrategyPreset({
      strategyId,
      name: strategyPresetName || `${strategyId}-${dayjs().format('MMDD-HHmm')}`,
      params: strategyParamsPayload,
    })
    setStrategyPresetId(preset.id)
    setStrategyPresetName(preset.name)
    setStrategyPresetRefreshTick((value) => value + 1)
    message.success(`已保存策略预设：${preset.name}`)
  }

  function handleApplyStrategyPreset() {
    if (!selectedStrategyPreset) {
      message.info('请先选择策略预设。')
      return
    }
    setStrategyParams(normalizeStrategyParams(selectedStrategyPreset.strategy_params))
    message.success(`已应用策略预设：${selectedStrategyPreset.name}`)
  }

  function handleDeleteStrategyPreset() {
    if (!selectedStrategyPreset) {
      message.info('请先选择策略预设。')
      return
    }
    deleteSharedStrategyPreset(strategyId, selectedStrategyPreset.id)
    setStrategyPresetId(null)
    setStrategyPresetRefreshTick((value) => value + 1)
    message.success(`已删除策略预设：${selectedStrategyPreset.name}`)
  }

  function handleCompareStrategyChange(values: Array<string | number>) {
    const normalized = Array.from(
      new Set(values.map((item) => String(item).trim()).filter((item) => item.length > 0)),
    ) as StrategyId[]
    if (normalized.length <= 0) {
      setCompareStrategyIds([strategyId])
      return
    }
    if (!normalized.includes(strategyId)) {
      normalized.unshift(strategyId)
    }
    setCompareStrategyIds(normalized.slice(0, 4))
  }

  function applyCompareViewSnapshot(snapshot: CompareViewSnapshot) {
    const normalized = normalizeCompareViewSnapshot(snapshot, strategyId)
    setCompareStrategyIds(normalized.strategy_ids)
    setCompareOnlyDifferences(normalized.only_differences)
    setCompareCategoryFilter(normalized.category)
  }

  function applyShareCode(raw: string): boolean {
    const snapshot = parseCompareViewShareCode(raw, strategyId)
    if (!snapshot) return false
    applyCompareViewSnapshot(snapshot)
    return true
  }

  async function copyTextToClipboard(text: string): Promise<boolean> {
    const normalized = String(text || '')
    if (!normalized) return false
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(normalized)
        return true
      } catch {
        // fallback to execCommand
      }
    }
    try {
      const textarea = document.createElement('textarea')
      textarea.value = normalized
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      textarea.style.pointerEvents = 'none'
      document.body.appendChild(textarea)
      textarea.select()
      const copied = document.execCommand('copy')
      textarea.remove()
      return copied
    } catch {
      return false
    }
  }

  function handleSaveComparePreset() {
    const preset = saveCompareViewPreset({
      fallbackStrategyId: strategyId,
      name: comparePresetName || `${strategyId}-compare-${dayjs().format('MMDD-HHmm')}`,
      snapshot: {
        strategy_ids: compareStrategyIds,
        only_differences: compareOnlyDifferences,
        category: compareCategoryFilter,
      },
    })
    setComparePresetId(preset.id)
    setComparePresetName(preset.name)
    setComparePresetRefreshTick((value) => value + 1)
    message.success(`已保存对比视图：${preset.name}`)
  }

  function handleApplyComparePreset() {
    if (!selectedComparePreset) {
      message.info('请先选择对比视图预设。')
      return
    }
    const snapshot = normalizeCompareViewSnapshot(selectedComparePreset, strategyId)
    applyCompareViewSnapshot(snapshot)
    message.success(`已应用对比视图：${selectedComparePreset.name}`)
  }

  function handleDeleteComparePreset() {
    if (!selectedComparePreset) {
      message.info('请先选择对比视图预设。')
      return
    }
    deleteCompareViewPreset(selectedComparePreset.id, strategyId)
    setComparePresetId(null)
    setComparePresetRefreshTick((value) => value + 1)
    message.success(`已删除对比视图：${selectedComparePreset.name}`)
  }

  function handleExportComparePresets() {
    const rows = loadCompareViewPresets(strategyId)
    if (rows.length <= 0) {
      message.info('暂无对比视图预设可导出。')
      return
    }
    const payload = {
      schema_version: COMPARE_VIEW_PRESETS_EXPORT_SCHEMA_VERSION,
      exported_at: new Date().toISOString(),
      presets: rows.map((item) => ({
        id: item.id,
        name: item.name,
        saved_at: item.saved_at,
        strategy_ids: item.strategy_ids,
        only_differences: item.only_differences,
        category: item.category,
      })),
    }
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `strategy-compare-presets-${dayjs().format('YYYYMMDD-HHmmss')}.json`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(url)
      message.success(`已导出 ${rows.length} 个对比视图预设。`)
    } catch {
      message.error('导出对比视图失败。')
    }
  }

  async function handleImportComparePresets(file: File) {
    try {
      const text = await file.text()
      const parsed = JSON.parse(text) as unknown
      const imported = normalizeImportedCompareViewPresets(parsed, strategyId)
      if (imported.length <= 0) {
        message.info('导入文件中没有可用的对比视图预设。')
        return
      }
      const current = loadCompareViewPresets(strategyId)
      const keySet = new Set<string>()
      const merged: CompareViewPreset[] = []
      imported.forEach((item) => {
        const key = snapshotIdentity(item)
        if (keySet.has(key)) return
        keySet.add(key)
        merged.push(item)
      })
      current.forEach((item) => {
        const key = snapshotIdentity(item)
        if (keySet.has(key)) return
        keySet.add(key)
        merged.push(item)
      })
      const next = merged.slice(0, COMPARE_VIEW_PRESETS_MAX_COUNT)
      saveCompareViewPresets(next, strategyId)
      setComparePresetRefreshTick((value) => value + 1)
      const first = next[0]
      if (first) {
        setComparePresetId(first.id)
        setComparePresetName(first.name)
      }
      message.success(`导入完成：共 ${imported.length} 条，新增 ${Math.max(0, next.length - current.length)} 条。`)
    } catch {
      message.error('导入失败：请检查 JSON 文件格式。')
    }
  }

  async function handleCopyCompareShareCode() {
    const snapshot: CompareViewSnapshot = {
      strategy_ids: compareStrategyIds,
      only_differences: compareOnlyDifferences,
      category: compareCategoryFilter,
    }
    const code = buildCompareViewShareCode(snapshot, strategyId)
    setCompareShareCode(code)
    syncCompareCodeToQuery(code)
    const copied = await copyTextToClipboard(code)
    if (copied) {
      message.success('已复制对比视图分享码。')
      return
    }
    message.warning('无法直接复制，请手动复制分享码。')
  }

  function handleApplyCompareShareCode() {
    const raw = compareShareCode.trim()
    if (!raw) {
      message.info('请先输入分享码。')
      return
    }
    const applied = applyShareCode(raw)
    if (!applied) {
      message.error('分享码无效，请检查后重试。')
      return
    }
    syncCompareCodeToQuery(raw)
    message.success('已通过分享码恢复对比视图。')
  }

  async function handlePasteAndApplyCompareShareCode() {
    try {
      if (typeof navigator === 'undefined' || !navigator.clipboard?.readText) {
        message.warning('当前环境不支持读取剪贴板，请手动粘贴分享码。')
        return
      }
      const text = (await navigator.clipboard.readText()).trim()
      if (!text) {
        message.info('剪贴板为空。')
        return
      }
      setCompareShareCode(text)
      const applied = applyShareCode(text)
      if (!applied) {
        message.error('剪贴板内容不是有效分享码。')
        return
      }
      syncCompareCodeToQuery(text)
      message.success('已从剪贴板恢复对比视图。')
    } catch {
      message.error('读取剪贴板失败，请手动粘贴分享码。')
    }
  }

  async function handleCopyCompareShareLink() {
    const snapshot: CompareViewSnapshot = {
      strategy_ids: compareStrategyIds,
      only_differences: compareOnlyDifferences,
      category: compareCategoryFilter,
    }
    const code = buildCompareViewShareCode(snapshot, strategyId)
    setCompareShareCode(code)
    syncCompareCodeToQuery(code)
    try {
      const url = new URL(window.location.href)
      url.searchParams.set('cv', code)
      const copied = await copyTextToClipboard(url.toString())
      if (copied) {
        message.success('已复制分享链接。')
      } else {
        message.warning('无法直接复制，请手动复制地址栏链接。')
      }
    } catch {
      message.warning('已写入链接参数，请手动复制地址栏链接。')
    }
  }

  return (
    <Space data-research="page" data-page="strategy" orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader
        title="策略中心"
        subtitle="集中维护策略参数与预设；选股漏斗/待买信号/策略回测页仅负责策略选择。"
        badge="集中配置"
      />

      <Card data-research="strategy-selector" className="glass-card" variant="borderless">
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Row data-research="fields" gutter={[12, 12]}>
            <Col xs={24} md={8}>
              <Typography.Text type="secondary">策略</Typography.Text>
              <Select
                loading={strategyCatalogQuery.isLoading}
                value={strategyId}
                onChange={(value) => {
                  const next = String(value) as StrategyId
                  setStrategyId(next)
                  setStrategyParams(getSharedStrategyParams(next))
                  setStrategyPresetId(null)
                }}
                options={(strategyItems.length > 0
                  ? strategyItems
                  : [{ strategy_id: 'wyckoff_trend_v1', name: '维科夫趋势V1', version: '1.0.0', enabled: true }])
                  .map((item) => ({
                    value: item.strategy_id,
                    label: `${item.name} (${item.version})${item.enabled === false ? ' - 已禁用' : ''}`,
                    disabled: item.enabled === false,
                  }))}
              />
            </Col>
            <Col xs={24} md={16}>
              <span className="research-inline-note">参数修改后自动同步到选股、策略运行和回测。</span>
            </Col>
          </Row>

          <details className="research-fold"><summary>策略信息与同步说明</summary><div className="research-fold-body"><DismissibleAlert
            dismissKey="strategy-center.strategy-info"
            type="info"
            showIcon
            title={selectedStrategy ? `当前策略：${selectedStrategy.name}` : '策略信息'}
            description={
              selectedStrategy
                ? `版本 ${selectedStrategy.version}，参数与选股、策略运行、回测共用。`
                : '未加载到策略目录，先按默认策略继续。'
            }
          />
            <DismissibleAlert
              dismissKey="strategy-center.sync-tip"
              type="success"
              showIcon
              title="配置会自动同步到业务页"
              description={
                <Space wrap size={8}>
                  <Typography.Text type="secondary">在下列页面只需切换策略，不再编辑参数。</Typography.Text>
                  <Link to="/screener">选股漏斗</Link>
                  <Link to="/signals">待买信号</Link>
                  <Link to="/backtest">策略回测</Link>
                  <Link to="/strategy/events">事件判别中心</Link>
                </Space>
              }
            /></div></details></Space>
      </Card>

      <Card data-research="strategy-parameters" className="glass-card" variant="borderless" title="参数模板区">
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            当前编辑策略参数会自动同步到业务页；保存为预设模板后可快速复用到同一策略。
          </Typography.Text>
          {strategyParamEntries.length > 0 ? (
            <Row data-research="fields" gutter={[12, 12]}>
              {strategyParamEntries.map((spec) => {
                const value = strategyParamsPayload[spec.key]
                if (spec.type === 'boolean') {
                  return (
                    <Col xs={24} md={8} lg={6} key={spec.key}>
                      <Typography.Text type="secondary">{spec.title}</Typography.Text>
                      <div className="strategy-param-switch">
                        <Switch checked={Boolean(value)} onChange={(checked) => updateStrategyParam(spec.key, checked)} />
                      </div>
                    </Col>
                  )
                }
                if (spec.type === 'enum') {
                  return (
                    <Col xs={24} md={8} lg={6} key={spec.key}>
                      <Typography.Text type="secondary">{spec.title}</Typography.Text>
                      <Select
                        value={typeof value === 'string' ? value : undefined}
                        options={spec.options.map((item) => ({
                          value: item,
                          label: resolveStrategyEnumOptionLabel(spec.key, item),
                        }))}
                        onChange={(next) => updateStrategyParam(spec.key, String(next))}
                        allowClear
                      />
                    </Col>
                  )
                }
                return (
                  <Col xs={24} md={8} lg={6} key={spec.key}>
                    <Typography.Text type="secondary">{spec.title}</Typography.Text>
                    <InputNumber
                      value={typeof value === 'number' ? value : undefined}
                      min={typeof spec.minimum === 'number' ? spec.minimum : undefined}
                      max={typeof spec.maximum === 'number' ? spec.maximum : undefined}
                      step={spec.type === 'integer' ? 1 : 0.1}
                      style={{ width: '100%' }}
                      onChange={(next) => {
                        if (next === null || next === undefined || Number.isNaN(Number(next))) {
                          updateStrategyParam(spec.key, undefined)
                          return
                        }
                        updateStrategyParam(spec.key, Number(next))
                      }}
                    />
                  </Col>
                )
              })}
            </Row>
          ) : (
            <Alert type="info" showIcon title="当前策略没有可编辑参数。" />
          )}

          <Divider style={{ margin: '4px 0' }} />

          <Typography.Text strong>预设模板</Typography.Text>
          <Row data-research="fields" gutter={[12, 12]}>
            <Col xs={24} md={8}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Typography.Text type="secondary">选择预设</Typography.Text>
                <Select
                  value={strategyPresetId ?? undefined}
                  placeholder="选择预设"
                  options={strategyPresets.map((item, index) => ({
                    value: item.id,
                    label: `预设#${index + 1} | ${item.name}`,
                  }))}
                  onChange={(value) => {
                    const nextId = String(value || '').trim()
                    if (!nextId) {
                      setStrategyPresetId(null)
                      return
                    }
                    setStrategyPresetId(nextId)
                    const matched = strategyPresets.find((item) => item.id === nextId)
                    if (matched) {
                      setStrategyPresetName(matched.name)
                    }
                  }}
                  allowClear
                />
              </Space>
            </Col>
            <Col xs={24} md={8}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Typography.Text type="secondary">预设名称</Typography.Text>
                <Input
                  value={strategyPresetName}
                  onChange={(event) => setStrategyPresetName(event.target.value)}
                  placeholder="输入预设名后保存"
                />
              </Space>
            </Col>
            <Col xs={24} md={8}>
              <Space style={{ marginTop: 22 }} wrap>
                <Button size="small" onClick={handleSaveStrategyPreset}>保存预设</Button>
                <Button size="small" onClick={handleApplyStrategyPreset}>应用预设</Button>
                <Button size="small" danger onClick={handleDeleteStrategyPreset}>删除预设</Button>
              </Space>
            </Col>
          </Row>
          {selectedStrategyPreset ? (
            <Alert
              type="info"
              showIcon
              title={`当前预设：${selectedStrategyPreset.name}`}
              description={`保存时间：${selectedStrategyPreset.saved_at}`}
            />
          ) : null}
        </Space>
      </Card>

      <details className="research-fold" ><summary>策略对比与视图预设</summary><div className="research-fold-body"><Card
        className="glass-card"
        variant="borderless"
        title="策略对比区"
        extra={<Typography.Text type="secondary">最多 4 个策略</Typography.Text>}
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Row gutter={[12, 12]}>
            <Col xs={24} lg={12}>
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                <Typography.Text type="secondary">选择对比策略</Typography.Text>
                <Select
                  mode="multiple"
                  value={compareStrategyIds}
                  onChange={handleCompareStrategyChange}
                  options={(strategyItems.length > 0
                    ? strategyItems
                    : [{ strategy_id: 'wyckoff_trend_v1', name: '维科夫趋势V1', version: '1.0.0', enabled: true }])
                    .map((item) => ({
                      value: item.strategy_id,
                      label: `${item.name} (${item.version})${item.enabled === false ? ' - 已禁用' : ''}`,
                      disabled: item.enabled === false,
                    }))}
                  maxTagCount={2}
                  placeholder="选择要并行对比的策略"
                />
                <Space wrap size={8}>
                  <Switch checked={compareOnlyDifferences} onChange={setCompareOnlyDifferences} />
                  <Typography.Text type="secondary">
                    仅看差异参数（{comparisonDiffCount}/{comparisonKeyOrder.length}）
                  </Typography.Text>
                </Space>
                <Space wrap size={8}>
                  <Typography.Text type="secondary">参数类别</Typography.Text>
                  <Select
                    value={compareCategoryFilter}
                    onChange={(value) => setCompareCategoryFilter(value as CompareParamCategory)}
                    style={{ minWidth: 190 }}
                    options={
                      (Object.keys(COMPARE_PARAM_CATEGORY_LABELS) as CompareParamCategory[]).map((key) => ({
                        value: key,
                        label: `${COMPARE_PARAM_CATEGORY_LABELS[key]} (${comparisonCategoryCounts[key] ?? 0})`,
                      }))
                    }
                  />
                </Space>
              </Space>
            </Col>
            <Col xs={24} lg={12}>
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                <Alert
                  type="info"
                  showIcon
                  title="对比说明"
                  description="用于并行查看策略能力、参数规模和当前有效参数，便于快速切换并调参。"
                />
                <Typography.Text type="secondary">对比视图预设</Typography.Text>
                <Select
                  value={comparePresetId ?? undefined}
                  placeholder="选择对比视图预设"
                  options={comparePresets.map((item, index) => ({
                    value: item.id,
                    label: `视图#${index + 1} | ${item.name}`,
                  }))}
                  onChange={(value) => {
                    const nextId = String(value || '').trim()
                    if (!nextId) {
                      setComparePresetId(null)
                      return
                    }
                    setComparePresetId(nextId)
                    const matched = comparePresets.find((item) => item.id === nextId)
                    if (matched) {
                      setComparePresetName(matched.name)
                    }
                  }}
                  allowClear
                />
                <Input
                  value={comparePresetName}
                  onChange={(event) => setComparePresetName(event.target.value)}
                  placeholder="输入对比视图名后保存"
                />
                <Space wrap size={8}>
                  <Button size="small" onClick={handleSaveComparePreset}>保存视图</Button>
                  <Button size="small" onClick={handleApplyComparePreset}>应用视图</Button>
                  <Button size="small" danger onClick={handleDeleteComparePreset}>删除视图</Button>
                  <Button size="small" onClick={handleExportComparePresets}>导出视图</Button>
                  <Button size="small" onClick={() => comparePresetImportInputRef.current?.click()}>导入视图</Button>
                </Space>
                <Divider style={{ margin: '4px 0' }} />
                <Typography.Text type="secondary">分享码</Typography.Text>
                <Input
                  value={compareShareCode}
                  onChange={(event) => setCompareShareCode(event.target.value)}
                  placeholder="粘贴分享码后可恢复对比视图"
                />
                <Space wrap size={8}>
                  <Button size="small" onClick={() => void handleCopyCompareShareCode()}>复制分享码</Button>
                  <Button size="small" onClick={() => void handleCopyCompareShareLink()}>复制分享链接</Button>
                  <Button size="small" onClick={handleApplyCompareShareCode}>应用分享码</Button>
                  <Button size="small" onClick={() => void handlePasteAndApplyCompareShareCode()}>粘贴并应用</Button>
                </Space>
                <input
                  ref={comparePresetImportInputRef}
                  type="file"
                  accept=".json,application/json"
                  aria-label="导入对比视图预设"
                  className="strategy-hidden-input"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    if (!file) return
                    void handleImportComparePresets(file)
                  }}
                />
              </Space>
            </Col>
          </Row>

          {selectedComparePreset ? (
            <Alert
              type="info"
              showIcon
              title={`当前对比视图：${selectedComparePreset.name}`}
              description={`保存时间：${selectedComparePreset.saved_at}`}
            />
          ) : null}

          <Row gutter={[12, 12]}>
            {comparisonItems.map(({ descriptor, schemaEntries, payload }) => {
              const entries = Object.entries(payload)
              const previewKeys = comparisonVisibleKeys.slice(0, 12)
              const hiddenCount = Math.max(0, comparisonVisibleKeys.length - previewKeys.length)
              const strategyDiffCount = comparisonVisibleKeys.filter((key) => comparisonDiffMap[key]).length
              return (
                <Col xs={24} md={12} xl={8} key={descriptor.strategy_id}>
                  <Card
                    size="small"
                    title={`${descriptor.name} (${descriptor.version})`}
                    extra={
                      descriptor.strategy_id === strategyId ? (
                        <Tag color="success">当前编辑</Tag>
                      ) : (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => {
                            setStrategyId(descriptor.strategy_id)
                            setStrategyParams(getSharedStrategyParams(descriptor.strategy_id))
                            setStrategyPresetId(null)
                          }}
                        >
                          切换编辑
                        </Button>
                      )
                    }
                  >
                    <Space orientation="vertical" size={6} style={{ width: '100%' }}>
                      <Space wrap size={[6, 6]}>
                        <Tag color={descriptor.capabilities?.supports_matrix ? 'success' : 'default'}>
                          矩阵: {descriptor.capabilities?.supports_matrix ? '支持' : '不支持'}
                        </Tag>
                        <Tag color={descriptor.capabilities?.supports_signal_age_filter ? 'success' : 'default'}>
                          年龄过滤: {descriptor.capabilities?.supports_signal_age_filter ? '支持' : '不支持'}
                        </Tag>
                        <Tag color={descriptor.capabilities?.supports_entry_delay ? 'success' : 'default'}>
                          延迟入场: {descriptor.capabilities?.supports_entry_delay ? '支持' : '不支持'}
                        </Tag>
                      </Space>
                      <Typography.Text type="secondary">
                        参数项：{schemaEntries.length} | 当前有效参数：{entries.length} | 对比参数：{comparisonVisibleKeys.length} | 差异项：{strategyDiffCount} | 类别：{COMPARE_PARAM_CATEGORY_LABELS[compareCategoryFilter]}
                      </Typography.Text>
                      <Typography.Text strong>
                        {compareOnlyDifferences ? '差异参数' : '参数对比（差异高亮）'}
                      </Typography.Text>
                      {previewKeys.length > 0 ? (
                        previewKeys.map((key) => {
                          const isDifferent = Boolean(comparisonDiffMap[key])
                          const category = comparisonKeyCategoryMap[key]
                          const paramLabel = resolveCompareParamLabel(key, comparisonKeyTitleMap[key])
                          return (
                            <Space key={`${descriptor.strategy_id}-${key}`} size={6} wrap>
                              <Tag color="blue">{COMPARE_PARAM_CATEGORY_LABELS[category]}</Tag>
                              <Tag color={isDifferent ? 'volcano' : 'default'}>{isDifferent ? '差异' : '一致'}</Tag>
                              <Typography.Text type={isDifferent ? 'danger' : 'secondary'}>
                                {paramLabel}: {formatParamValue(payload[key])}
                              </Typography.Text>
                              <Typography.Text type="secondary">[{key}]</Typography.Text>
                            </Space>
                          )
                        })
                      ) : (
                        <Typography.Text type="secondary">
                          {compareOnlyDifferences
                            ? `当前筛选（${COMPARE_PARAM_CATEGORY_LABELS[compareCategoryFilter]} + 仅差异）下暂无参数。`
                            : `当前筛选（${COMPARE_PARAM_CATEGORY_LABELS[compareCategoryFilter]}）下暂无参数。`}
                        </Typography.Text>
                      )}
                      {hiddenCount > 0 ? (
                        <Typography.Text type="secondary">... 其余 {hiddenCount} 项</Typography.Text>
                      ) : null}
                    </Space>
                  </Card>
                </Col>
              )
            })}
          </Row>
        </Space>
      </Card></div></details>
    </Space>
  )
}
