import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Checkbox,
  Col,
  Divider,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Typography,
} from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Controller, useFieldArray, useForm, useWatch } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import {
  backfillWyckoffEventStore,
  getAIPlaybook,
  getAILocalPlaybook,
  getAIQuickPrompts,
  getAISessions,
  getAIUsage,
  getConfig,
  getSystemStorage,
  getWyckoffEventStoreStats,
  putAILocalPlaybook,
  putAIQuickPrompts,
  testAIProvider,
  updateConfig,
} from '@/shared/api/endpoints'
import { resetAllDismissedAlerts } from '@/shared/components/DismissibleAlert'
import { PageHeader } from '@/shared/components/PageHeader'
import type { AppConfig, Market } from '@/types/contracts'

const providerSchema = z
  .object({
    id: z.string().min(1, 'ID 必填'),
    label: z.string().min(1, '名称必填'),
    base_url: z.string().url('请输入有效 URL'),
    model: z.string().min(1, '模型必填'),
    api_key: z.string(),
    api_key_path: z.string(),
    enabled: z.boolean(),
  })
  .superRefine((value, ctx) => {
    const hasInlineKey = value.api_key.trim().length > 0
    const hasPath = value.api_key_path.trim().length > 0
    if (!hasInlineKey && !hasPath) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: '请至少填写 API 密钥 或 API Key 路径',
        path: ['api_key'],
      })
    }
  })

const sourceSchema = z.object({
  id: z.string().min(1, 'ID 必填'),
  name: z.string().min(1, '名称必填'),
  url: z.string().url('请输入有效 URL'),
  enabled: z.boolean(),
})

const schema = z.object({
  tdx_data_path: z.string().min(3),
  market_data_source: z.enum(['tdx_only', 'tdx_then_akshare', 'akshare_only']),
  akshare_cache_dir: z.string(),
  markets: z.array(z.enum(['sh', 'sz', 'bj'])).min(1),
  return_window_days: z.number().min(5).max(120),
  candles_window_bars: z.number().int().min(120).max(5000),
  backtest_matrix_engine_enabled: z.boolean(),
  backtest_default_execution_path_preference: z.enum(['auto', 'matrix', 'legacy']),
  backtest_plateau_workers: z.number().int().min(1).max(32),
  top_n: z.number().min(10).max(2000),
  turnover_threshold: z.number().min(0.01).max(0.2),
  amount_threshold: z.number().min(5e7).max(5e9),
  amplitude_threshold: z.number().min(0.01).max(0.15),
  initial_capital: z.number().min(10_000).max(100_000_000),
  ai_provider: z.string().min(1),
  ai_timeout_sec: z.number().min(3).max(60),
  ai_retry_count: z.number().min(0).max(5),
  api_key: z.string(),
  api_key_path: z.string(),
  ai_providers: z.array(providerSchema).min(1, '至少保留一个 AI Provider'),
  ai_sources: z.array(sourceSchema).min(1, '至少保留一个信息源'),
})

type FormValues = z.infer<typeof schema>

type ProviderPresetKey = 'openai' | 'deepseek' | 'qwen' | 'anthropic' | 'gemini' | 'custom'

type ProviderPreset = {
  key: ProviderPresetKey
  label: string
  idBase: string
  nameBase: string
  baseUrl: string
  model: string
}

const AI_PROVIDER_PRESETS: ProviderPreset[] = [
  {
    key: 'openai',
    label: 'OpenAI',
    idBase: 'openai',
    nameBase: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
  },
  {
    key: 'deepseek',
    label: 'DeepSeek',
    idBase: 'deepseek',
    nameBase: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
  },
  {
    key: 'qwen',
    label: 'Qwen',
    idBase: 'qwen',
    nameBase: 'Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus',
  },
  {
    key: 'anthropic',
    label: 'Anthropic',
    idBase: 'anthropic',
    nameBase: 'Anthropic',
    baseUrl: 'https://api.anthropic.com/v1',
    model: 'claude-3-5-sonnet-20241022',
  },
  {
    key: 'gemini',
    label: 'Google Gemini',
    idBase: 'gemini',
    nameBase: 'Google Gemini',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    model: 'gemini-2.0-flash',
  },
  {
    key: 'custom',
    label: '自定义',
    idBase: 'custom',
    nameBase: '自定义Provider',
    baseUrl: 'https://your-provider.example.com/v1',
    model: 'custom-model',
  },
]

const AI_PROVIDER_PRESET_OPTIONS = AI_PROVIDER_PRESETS.map((item) => ({
  label: item.label,
  value: item.key,
}))

function pickUniqueValue(base: string, existing: Set<string>): string {
  const normalized = base.trim()
  if (!existing.has(normalized)) return normalized
  let idx = 2
  while (existing.has(`${normalized}-${idx}`)) {
    idx += 1
  }
  return `${normalized}-${idx}`
}

function buildProviderFromPreset(
  presetKey: ProviderPresetKey,
  existingProviders: FormValues['ai_providers'],
): FormValues['ai_providers'][number] {
  const preset = AI_PROVIDER_PRESETS.find((item) => item.key === presetKey) ?? AI_PROVIDER_PRESETS[0]
  const existingIds = new Set(existingProviders.map((item) => item.id.trim()).filter(Boolean))
  const existingLabels = new Set(existingProviders.map((item) => item.label.trim()).filter(Boolean))
  const id = pickUniqueValue(preset.idBase, existingIds)
  const label = pickUniqueValue(preset.nameBase, existingLabels)
  return {
    id,
    label,
    base_url: preset.baseUrl,
    model: preset.model,
    api_key: '',
    api_key_path: `%USERPROFILE%\\.tdx-trend\\${id}.key`,
    enabled: true,
  }
}

function newSource(nextIndex: number): FormValues['ai_sources'][number] {
  return {
    id: `source-${nextIndex}`,
    name: `自定义信息源-${nextIndex}`,
    url: 'https://example.com/',
    enabled: true,
  }
}

function defaultPrimarySource(): FormValues['ai_sources'][number] {
  return {
    id: 'eastmoney',
    name: '东方财富新闻',
    url: 'https://finance.eastmoney.com/',
    enabled: true,
  }
}

const DEFAULT_FORM_VALUES: FormValues = {
  tdx_data_path: 'D:\\new_tdx\\vipdoc',
  market_data_source: 'tdx_then_akshare',
  akshare_cache_dir: '%USERPROFILE%\\.tdx-trend\\akshare\\daily',
  markets: ['sh', 'sz'],
  return_window_days: 40,
  candles_window_bars: 120,
  backtest_matrix_engine_enabled: true,
  backtest_default_execution_path_preference: 'legacy',
  backtest_plateau_workers: 4,
  top_n: 500,
  turnover_threshold: 0.05,
  amount_threshold: 5e8,
  amplitude_threshold: 0.03,
  initial_capital: 1_000_000,
  ai_provider: 'openai',
  ai_timeout_sec: 10,
  ai_retry_count: 2,
  api_key: '',
  api_key_path: '',
  ai_providers: [buildProviderFromPreset('openai', [])],
  ai_sources: [defaultPrimarySource()],
}

export function SettingsPage() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const isMockEnabled = import.meta.env.DEV && import.meta.env.VITE_ENABLE_MSW === 'true'
  const configQuery = useQuery({
    queryKey: ['config'],
    queryFn: getConfig,
  })
  const storageQuery = useQuery({
    queryKey: ['system-storage'],
    queryFn: getSystemStorage,
  })
  const wyckoffStatsQuery = useQuery({
    queryKey: ['wyckoff-event-store-stats'],
    queryFn: getWyckoffEventStoreStats,
  })
  const [backfillDateFrom, setBackfillDateFrom] = useState(dayjs().subtract(20, 'day').format('YYYY-MM-DD'))
  const [backfillDateTo, setBackfillDateTo] = useState(dayjs().subtract(1, 'day').format('YYYY-MM-DD'))
  const [backfillMarkets, setBackfillMarkets] = useState<Market[]>(['sh', 'sz'])
  const [backfillWindowsText, setBackfillWindowsText] = useState('60')
  const [backfillMaxSymbols, setBackfillMaxSymbols] = useState(300)
  const [backfillForceRebuild, setBackfillForceRebuild] = useState(false)

  const { control, getValues, handleSubmit, reset, setValue } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: DEFAULT_FORM_VALUES,
  })
  const [testingProviderId, setTestingProviderId] = useState<string | null>(null)
  const [providerPresetToAdd, setProviderPresetToAdd] = useState<ProviderPresetKey>('openai')

  const providerArray = useFieldArray({
    control,
    name: 'ai_providers',
  })

  const sourceArray = useFieldArray({
    control,
    name: 'ai_sources',
  })

  const providers =
    useWatch({
      control,
      name: 'ai_providers',
    }) ?? []

  const providerOptions = providers.map((provider) => ({
    label: `${provider.label} (${provider.id})`,
    value: provider.id,
  }))

  useEffect(() => {
    if (!configQuery.data) return
    reset({
      ...DEFAULT_FORM_VALUES,
      ...configQuery.data,
    })
  }, [configQuery.data, reset])

  const saveMutation = useMutation({
    mutationFn: (values: FormValues) => updateConfig(values as AppConfig),
    onSuccess: () => {
      message.success('配置已保存')
      void queryClient.invalidateQueries({ queryKey: ['config'] })
      void queryClient.invalidateQueries({ queryKey: ['system-storage'] })
    },
  })
  const wyckoffBackfillMutation = useMutation({
    mutationFn: backfillWyckoffEventStore,
    onSuccess: (data) => {
      message.success(data.message)
      void queryClient.invalidateQueries({ queryKey: ['wyckoff-event-store-stats'] })
      void queryClient.invalidateQueries({ queryKey: ['system-storage'] })
    },
    onError: () => {
      message.error('事件库回填失败，请检查日期范围与数据源配置。')
    },
  })

  function validateAndSave(values: FormValues) {
    const hasCredential = (provider: FormValues['ai_providers'][number]) =>
      provider.api_key.trim().length > 0 || provider.api_key_path.trim().length > 0

    const providerIdSet = new Set<string>()
    for (const provider of values.ai_providers) {
      if (providerIdSet.has(provider.id)) {
        message.error(`Provider ID 重复: ${provider.id}`)
        return
      }
      providerIdSet.add(provider.id)

      if (provider.enabled && !hasCredential(provider)) {
        message.error(`Provider ${provider.id} 缺少凭证，请填写 API 密钥或 Key 路径`)
        return
      }
    }

    const activeProvider = values.ai_providers.find((provider) => provider.id === values.ai_provider)
    if (!activeProvider) {
      message.error('当前激活 Provider 不存在，请重新选择')
      return
    }
    if (!activeProvider.enabled) {
      message.error('当前激活 Provider 已禁用，请切换到可用 Provider')
      return
    }
    if (!hasCredential(activeProvider)) {
      message.error('当前激活 Provider 缺少凭证，请填写 API 密钥或 Key 路径')
      return
    }

    saveMutation.mutate({
      ...values,
    })
  }

  async function handleProviderTest(index: number) {
    const values = getValues()
    const provider = values.ai_providers[index]
    if (!provider) {
      message.error('Provider 不存在')
      return
    }

    setTestingProviderId(provider.id)
    try {
      const result = await testAIProvider({
        provider,
        fallback_api_key: '',
        fallback_api_key_path: '',
        timeout_sec: values.ai_timeout_sec,
      })
      if (result.ok) {
        message.success(`[${provider.id}] ${result.message}`)
      } else {
        message.warning(`[${provider.id}] ${result.message}${result.error_code ? ` (${result.error_code})` : ''}`)
      }
    } catch {
      message.error(`[${provider.id}] 测试失败，请检查网络与配置`)
    } finally {
      setTestingProviderId(null)
    }
  }

  function handleAppendProviderFromPreset() {
    const values = getValues()
    const currentProviders = values.ai_providers ?? []
    const created = buildProviderFromPreset(providerPresetToAdd, currentProviders)
    providerArray.append(created)
    if (!values.ai_provider?.trim()) {
      setValue('ai_provider', created.id)
    }
  }

  function handleWyckoffBackfill() {
    if (wyckoffBackfillMutation.isPending) return
    const datePattern = /^\d{4}-\d{2}-\d{2}$/
    const dateFrom = backfillDateFrom.trim()
    const dateTo = backfillDateTo.trim()
    if (!datePattern.test(dateFrom) || !datePattern.test(dateTo)) {
      message.error('回填日期格式需为 YYYY-MM-DD')
      return
    }
    const windowDaysList = Array.from(
      new Set(
        backfillWindowsText
          .split(/[,\s]+/)
          .map((item) => Number(item))
          .filter((item) => Number.isFinite(item) && item >= 20 && item <= 240),
      ),
    )
    if (windowDaysList.length === 0) {
      message.error('请至少输入一个有效窗口（20~240）')
      return
    }
    wyckoffBackfillMutation.mutate({
      date_from: dateFrom,
      date_to: dateTo,
      markets: backfillMarkets,
      window_days_list: windowDaysList,
      max_symbols_per_day: backfillMaxSymbols,
      force_rebuild: backfillForceRebuild,
    })
  }

  function handleResetDismissedAlerts() {
    const removedCount = resetAllDismissedAlerts()
    if (removedCount > 0) {
      message.success(`已恢复 ${removedCount} 条提示`)
      return
    }
    message.info('当前没有已关闭的提示条')
  }

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <PageHeader title="系统设置" subtitle="支持自定义 AI Provider 与信息源，满足多来源接入。" />
      {isMockEnabled ? (
        <Alert
          type="warning"
          showIcon
          title="当前启用了 MSW Mock 模式，交易/复盘/AI 记录不会写入真实后端持久化文件。"
        />
      ) : null}
      <Card className="glass-card" variant="borderless">
        <Form layout="vertical" onFinish={handleSubmit(validateAndSave)}>
          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Form.Item label="TDX 数据路径">
                <Controller
                  name="tdx_data_path"
                  control={control}
                  render={({ field }) => <Input {...field} />}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <Form.Item label="行情数据源策略">
                <Controller
                  name="market_data_source"
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onChange={field.onChange}
                      options={[
                        { label: '优先 TDX，缺失回退 AkShare（推荐）', value: 'tdx_then_akshare' },
                        { label: '仅 TDX', value: 'tdx_only' },
                        { label: '仅 AkShare 缓存', value: 'akshare_only' },
                      ]}
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <Form.Item label="本地行情目录（CSV）">
                <Controller
                  name="akshare_cache_dir"
                  control={control}
                  render={({ field }) => (
                    <Input
                      {...field}
                      placeholder="%USERPROFILE%\\.tdx-trend\\market-data\\daily"
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <Form.Item label="本地行情目录候选">
                <Select
                  allowClear
                  showSearch
                  placeholder={
                    storageQuery.data?.akshare_cache_candidates.length
                      ? '从已发现目录中选择'
                      : '暂无已发现目录，可手动填写'
                  }
                  options={(storageQuery.data?.akshare_cache_candidates ?? []).map((value) => ({
                    value,
                    label: value,
                  }))}
                  onChange={(value) => setValue('akshare_cache_dir', value || '')}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <Form.Item label="市场">
                <Controller
                  name="markets"
                  control={control}
                  render={({ field }) => (
                    <Checkbox.Group
                      value={field.value}
                      onChange={(value) => field.onChange(value as FormValues['markets'])}
                      options={[
                        { label: '沪市', value: 'sh' },
                        { label: '深市', value: 'sz' },
                        { label: '北交所', value: 'bj' },
                      ]}
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={24} md={12}>
              <Form.Item label="激活 AI Provider">
                <Controller
                  name="ai_provider"
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onChange={field.onChange}
                      options={providerOptions}
                      placeholder="请选择激活 Provider"
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={12} md={6}>
              <Form.Item label="AI 超时(秒)">
                <Controller
                  name="ai_timeout_sec"
                  control={control}
                  render={({ field }) => (
                    <InputNumber
                      min={3}
                      max={60}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? 10)}
                      style={{ width: '100%' }}
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={12} md={6}>
              <Form.Item label="AI 重试次数">
                <Controller
                  name="ai_retry_count"
                  control={control}
                  render={({ field }) => (
                    <InputNumber
                      min={0}
                      max={5}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? 2)}
                      style={{ width: '100%' }}
                    />
                  )}
                />
              </Form.Item>
            </Col>

            <Col xs={12} md={6}>
              <Form.Item label="模拟初始资金">
                <Controller
                  name="initial_capital"
                  control={control}
                  render={({ field }) => (
                    <InputNumber
                      min={10_000}
                      max={100_000_000}
                      step={10_000}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? 1_000_000)}
                      style={{ width: '100%' }}
                    />
                  )}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item label="K线读取窗口(根)">
                <Controller
                  name="candles_window_bars"
                  control={control}
                  render={({ field }) => (
                    <InputNumber
                      min={120}
                      max={5000}
                      step={60}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? 120)}
                      style={{ width: '100%' }}
                    />
                  )}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item label="矩阵回测引擎">
                <Controller
                  name="backtest_matrix_engine_enabled"
                  control={control}
                  render={({ field }) => (
                    <Switch
                      checked={Boolean(field.value)}
                      onChange={field.onChange}
                      checkedChildren="开启"
                      unCheckedChildren="关闭"
                    />
                  )}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item label="默认执行路径">
                <Controller
                  name="backtest_default_execution_path_preference"
                  control={control}
                  render={({ field }) => (
                    <Select
                      value={field.value}
                      onChange={field.onChange}
                      options={[
                        { label: '旧路径 legacy', value: 'legacy' },
                        { label: '自动 auto', value: 'auto' },
                        { label: '矩阵 matrix', value: 'matrix' },
                      ]}
                    />
                  )}
                />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item label="收益平原并行线程">
                <Controller
                  name="backtest_plateau_workers"
                  control={control}
                  render={({ field }) => (
                    <InputNumber
                      min={1}
                      max={32}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? 4)}
                      style={{ width: '100%' }}
                    />
                  )}
                />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <Typography.Text type="secondary">
                行情数据源可选 TDX 或本地行情目录回退（可由 AkShare/Baostock 生成 CSV）。漏斗参数（涨幅窗口、TopN、20日阈值）已统一在选股漏斗页面配置，此处仅保留系统级参数。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                矩阵回测引擎用于控制后端是否允许矩阵能力；默认执行路径用于决定回测页默认发送 auto / legacy / matrix；收益平原并行线程用于控制参数点评估并发度（环境变量可覆盖该值）。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                本地状态: 配置{storageQuery.data?.app_state_exists ? '已持久化' : '未持久化'}，
                模拟账户{storageQuery.data?.sim_state_exists ? '已持久化' : '未持久化'}，
                本地行情文件数 {storageQuery.data?.akshare_cache_file_count ?? 0}，
                威科夫事件库{storageQuery.data?.wyckoff_event_store_exists ? '已就绪' : '未创建'}。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                事件库统计: 记录 {wyckoffStatsQuery.data?.db_record_count ?? 0}，命中{' '}
                {wyckoffStatsQuery.data?.cache_hits ?? 0}，未命中 {wyckoffStatsQuery.data?.cache_misses ?? 0}，
                lazy-fill 写入 {wyckoffStatsQuery.data?.lazy_fill_writes ?? 0}，回填写入{' '}
                {wyckoffStatsQuery.data?.backfill_writes ?? 0}。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                命中率 {(Number(wyckoffStatsQuery.data?.cache_hit_rate ?? 0) * 100).toFixed(2)}%，缺失率{' '}
                {(Number(wyckoffStatsQuery.data?.cache_miss_rate ?? 0) * 100).toFixed(2)}%，质量告警（累计）:
                空事件 {wyckoffStatsQuery.data?.quality_empty_events ?? 0}、异常分值{' '}
                {wyckoffStatsQuery.data?.quality_score_outliers ?? 0}、日期错位{' '}
                {wyckoffStatsQuery.data?.quality_date_misaligned ?? 0}；读取次数{' '}
                {wyckoffStatsQuery.data?.snapshot_reads ?? 0}，平均读取耗时{' '}
                {Number(wyckoffStatsQuery.data?.avg_snapshot_read_ms ?? 0).toFixed(3)} ms。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                最近一次回填质量告警: 空事件 {wyckoffStatsQuery.data?.last_backfill_quality_empty_events ?? 0}、
                异常分值 {wyckoffStatsQuery.data?.last_backfill_quality_score_outliers ?? 0}、日期错位{' '}
                {wyckoffStatsQuery.data?.last_backfill_quality_date_misaligned ?? 0}。
              </Typography.Text>
              <br />
              <Typography.Text type="secondary">
                回填参数：日期区间 + 市场 + 窗口列表（逗号分隔），用于批量补齐缺失事件记录。
              </Typography.Text>
              <Space wrap style={{ marginTop: 8 }}>
                <Input
                  style={{ width: 130 }}
                  placeholder="date_from"
                  value={backfillDateFrom}
                  onChange={(event) => setBackfillDateFrom(event.target.value)}
                />
                <Input
                  style={{ width: 130 }}
                  placeholder="date_to"
                  value={backfillDateTo}
                  onChange={(event) => setBackfillDateTo(event.target.value)}
                />
                <Select
                  mode="multiple"
                  style={{ minWidth: 180 }}
                  value={backfillMarkets}
                  onChange={(value) => setBackfillMarkets(value as Market[])}
                  options={[
                    { label: '沪市', value: 'sh' },
                    { label: '深市', value: 'sz' },
                    { label: '北交所', value: 'bj' },
                  ]}
                />
                <Input
                  style={{ width: 140 }}
                  placeholder="窗口: 60,90"
                  value={backfillWindowsText}
                  onChange={(event) => setBackfillWindowsText(event.target.value)}
                />
                <InputNumber
                  min={20}
                  max={6000}
                  value={backfillMaxSymbols}
                  onChange={(value) => setBackfillMaxSymbols(Number(value || 300))}
                />
                <Switch
                  checked={backfillForceRebuild}
                  onChange={setBackfillForceRebuild}
                  checkedChildren="强制重算"
                  unCheckedChildren="仅补缺失"
                />
                <Button
                  loading={wyckoffBackfillMutation.isPending}
                  disabled={wyckoffStatsQuery.data?.enabled === false}
                  onClick={handleWyckoffBackfill}
                >
                  回填事件库
                </Button>
              </Space>
              {Array.isArray(wyckoffBackfillMutation.data?.warnings) &&
              (wyckoffBackfillMutation.data?.warnings?.length ?? 0) > 0 ? (
                <Alert
                  style={{ marginTop: 8 }}
                  type="warning"
                  showIcon
                  title={wyckoffBackfillMutation.data?.warnings.join('；')}
                />
              ) : null}
              <Space wrap style={{ marginTop: 8 }}>
                <Button onClick={handleResetDismissedAlerts}>恢复已关闭提示条</Button>
                <Typography.Text type="secondary">
                  用于恢复选股漏斗、待买信号、回测、策略中心顶部信息提示。
                </Typography.Text>
              </Space>
            </Col>
          </Row>

          <Divider titlePlacement="start">AI Provider 列表</Divider>
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            {providerArray.fields.map((item, index) => (
              <Card key={item.id} className="glass-card" size="small" variant="borderless">
                <Row gutter={[10, 10]}>
                  <Col xs={24} md={6}>
                    <Form.Item label="Provider ID">
                      <Controller
                        name={`ai_providers.${index}.id`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={6}>
                    <Form.Item label="显示名称">
                      <Controller
                        name={`ai_providers.${index}.label`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={8}>
                    <Form.Item label="Base URL">
                      <Controller
                        name={`ai_providers.${index}.base_url`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={4}>
                    <Form.Item label="启用">
                      <Controller
                        name={`ai_providers.${index}.enabled`}
                        control={control}
                        render={({ field }) => (
                          <Switch
                            checked={Boolean(field.value)}
                            onChange={field.onChange}
                            checkedChildren="开"
                            unCheckedChildren="关"
                          />
                        )}
                      />
                    </Form.Item>
                  </Col>

                  <Col xs={24} md={6}>
                    <Form.Item label="默认模型">
                      <Controller
                        name={`ai_providers.${index}.model`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={9}>
                    <Form.Item label="API 密钥（可选）">
                      <Controller
                        name={`ai_providers.${index}.api_key`}
                        control={control}
                        render={({ field }) => <Input.Password {...field} placeholder="直接填写优先使用" />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={9}>
                    <Form.Item label="API Key 路径（可选）">
                      <Controller
                        name={`ai_providers.${index}.api_key_path`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                </Row>

                <Space>
                  <Button
                    onClick={() => {
                      void handleProviderTest(index)
                    }}
                    loading={testingProviderId === (providers[index]?.id ?? '')}
                  >
                    测试可用性
                  </Button>
                  <Button
                    danger
                    icon={<DeleteOutlined />}
                    disabled={providerArray.fields.length <= 1}
                    onClick={() => providerArray.remove(index)}
                  >
                    删除 Provider
                  </Button>
                </Space>
              </Card>
            ))}

            <Space wrap>
              <Select
                style={{ minWidth: 220 }}
                value={providerPresetToAdd}
                options={AI_PROVIDER_PRESET_OPTIONS}
                onChange={(value) => setProviderPresetToAdd(value as ProviderPresetKey)}
              />
              <Button
                icon={<PlusOutlined />}
                onClick={handleAppendProviderFromPreset}
              >
                按预设新增 Provider
              </Button>
            </Space>
          </Space>

          <Divider titlePlacement="start">AI 信息源网站</Divider>
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            {sourceArray.fields.map((item, index) => (
              <Card key={item.id} className="glass-card" size="small" variant="borderless">
                <Row gutter={[10, 10]}>
                  <Col xs={24} md={4}>
                    <Form.Item label="来源 ID">
                      <Controller
                        name={`ai_sources.${index}.id`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={5}>
                    <Form.Item label="来源名称">
                      <Controller
                        name={`ai_sources.${index}.name`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={13}>
                    <Form.Item label="URL">
                      <Controller
                        name={`ai_sources.${index}.url`}
                        control={control}
                        render={({ field }) => <Input {...field} />}
                      />
                    </Form.Item>
                  </Col>
                  <Col xs={12} md={2}>
                    <Form.Item label="启用">
                      <Controller
                        name={`ai_sources.${index}.enabled`}
                        control={control}
                        render={({ field }) => (
                          <Switch
                            checked={Boolean(field.value)}
                            onChange={field.onChange}
                            checkedChildren="开"
                            unCheckedChildren="关"
                          />
                        )}
                      />
                    </Form.Item>
                  </Col>
                </Row>

                <Button
                  danger
                  icon={<DeleteOutlined />}
                  disabled={sourceArray.fields.length <= 1}
                  onClick={() => sourceArray.remove(index)}
                >
                  删除信息源
                </Button>
              </Card>
            ))}

            <Button
              icon={<PlusOutlined />}
              onClick={() => sourceArray.append(newSource(sourceArray.fields.length + 1))}
            >
              新增信息源
            </Button>
          </Space>

          <Space orientation="vertical" size={6} style={{ marginTop: 14 }}>
            <Typography.Text type="warning">
              每个 Provider 需配置 API 密钥或 Key 路径之一，优先读取 `api_key`。
            </Typography.Text>
            <Typography.Text type="warning">
              当前仍为本地明文存储，请使用最小权限密钥并定期轮换。
            </Typography.Text>
            <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
              保存配置
            </Button>
          </Space>
        </Form>
      </Card>

      <AIAssistantSettingsPanel />
    </Space>
  )
}

function AIAssistantSettingsPanel() {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()
  const [principlesDraft, setPrinciplesDraft] = useState('')
  const [promptLabel, setPromptLabel] = useState('')
  const [promptText, setPromptText] = useState('')
  const [promptPage, setPromptPage] = useState('chart')
  const [playbookPreview, setPlaybookPreview] = useState('')

  const localPlaybookQuery = useQuery({
    queryKey: ['ai-local-playbook'],
    queryFn: getAILocalPlaybook,
  })
  const quickPromptsQuery = useQuery({
    queryKey: ['ai-quick-prompts'],
    queryFn: getAIQuickPrompts,
  })
  const usageQuery = useQuery({
    queryKey: ['ai-usage'],
    queryFn: getAIUsage,
    staleTime: 30_000,
  })
  const sessionsQuery = useQuery({
    queryKey: ['ai-sessions'],
    queryFn: () => getAISessions(10),
    staleTime: 30_000,
  })

  useEffect(() => {
    const principles = localPlaybookQuery.data?.principles ?? []
    setPrinciplesDraft(principles.join('\n'))
  }, [localPlaybookQuery.data?.principles])

  async function savePrinciples() {
    const principles = principlesDraft
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    try {
      await putAILocalPlaybook(principles)
      await queryClient.invalidateQueries({ queryKey: ['ai-local-playbook'] })
      message.success('个人交易原则已保存')
    } catch {
      message.error('保存失败')
    }
  }

  async function addQuickPrompt() {
    const label = promptLabel.trim()
    const prompt = promptText.trim()
    if (!label || !prompt) return
    const templates = [
      ...(quickPromptsQuery.data?.templates ?? []),
      {
        id: `custom-${Date.now()}`,
        page: promptPage,
        label,
        prompt,
        pinned: false,
      },
    ]
    try {
      await putAIQuickPrompts(templates)
      await queryClient.invalidateQueries({ queryKey: ['ai-quick-prompts'] })
      setPromptLabel('')
      setPromptText('')
      message.success('快捷提问已添加')
    } catch {
      message.error('保存快捷提问失败')
    }
  }

  async function loadPlaybookPreview() {
    try {
      const data = await getAIPlaybook({ scope: 'all' })
      setPlaybookPreview(data.text.slice(0, 1200))
    } catch {
      message.error('加载 Playbook 失败')
    }
  }

  return (
    <Card className="glass-card" variant="borderless" title="AI 助手">
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <div>
          <Typography.Text strong>个人交易原则</Typography.Text>
          <Input.TextArea
            rows={4}
            value={principlesDraft}
            onChange={(event) => setPrinciplesDraft(event.target.value)}
            placeholder="每行一条，例如：只做主板、单笔止损不超过5%"
            style={{ marginTop: 8 }}
          />
          <Button style={{ marginTop: 8 }} onClick={() => void savePrinciples()}>
            保存原则
          </Button>
        </div>

        <Divider titlePlacement="start">自定义快捷提问</Divider>
        <Row gutter={[8, 8]}>
          <Col xs={24} md={6}>
            <Select
              style={{ width: '100%' }}
              value={promptPage}
              onChange={setPromptPage}
              options={[
                { value: 'chart', label: 'K线' },
                { value: 'screener', label: '选股' },
                { value: 'backtest', label: '回测' },
                { value: 'strategy', label: '策略' },
                { value: 'signals', label: '信号' },
                { value: 'review', label: '复盘' },
                { value: 'generic', label: '通用' },
              ]}
            />
          </Col>
          <Col xs={24} md={6}>
            <Input value={promptLabel} onChange={(e) => setPromptLabel(e.target.value)} placeholder="按钮标签" />
          </Col>
          <Col xs={24} md={8}>
            <Input value={promptText} onChange={(e) => setPromptText(e.target.value)} placeholder="提问内容" />
          </Col>
          <Col xs={24} md={4}>
            <Button type="primary" onClick={() => void addQuickPrompt()}>添加</Button>
          </Col>
        </Row>
        <Typography.Text type="secondary">
          已有 {quickPromptsQuery.data?.templates.length ?? 0} 条自定义快捷问（在 AI 助手抽屉中按页面显示）。
        </Typography.Text>

        <Divider titlePlacement="start">用量与会话</Divider>
        <Space wrap>
          <Typography.Text>
            Token 累计：{usageQuery.data?.total_tokens ?? 0}
            （输入 {usageQuery.data?.prompt_tokens ?? 0} / 输出 {usageQuery.data?.completion_tokens ?? 0}）
          </Typography.Text>
          <Typography.Text type="secondary">
            最近会话 {sessionsQuery.data?.items.length ?? 0} 条
          </Typography.Text>
        </Space>

        <Space>
          <Button onClick={() => void loadPlaybookPreview()}>预览 Playbook 摘要</Button>
        </Space>
        {playbookPreview ? (
          <Input.TextArea rows={6} value={playbookPreview} readOnly />
        ) : null}
      </Space>
    </Card>
  )
}
