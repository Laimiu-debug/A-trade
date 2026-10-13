import { useEffect, useMemo, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Col,
  Collapse,
  InputNumber,
  Row,
  Slider,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { PageHeader } from '@/shared/components/PageHeader'
import { useRegisterPageAIContext } from '@/shared/ai/PageAIContext'
import { getSentimentValuationQuote } from '@/shared/api/endpoints'
import {
  FORMULA_TEXT,
  INDUSTRY_PE_OPTIONS,
  VALUATION_PRESETS,
  calcTheoreticalCap,
  defaultFormValues,
  formatCapYi,
  formatCoef,
  presetToFormValues,
  sentimentColor,
  sentimentLabel,
} from '@/shared/utils/sentimentValuation'
import { searchStockLibrary, type StockSearchResult } from '@/shared/services/stockLibrary'
import { buildPageTitle } from '@/state/aiAssistantStore'
import type { SentimentValuationQuoteResponse, ValuationFormValues, ValuationPreset } from '@/types/contracts'
import styles from './SentimentValuationPage.module.css'

function round1(value: number) {
  return Math.round(value * 10) / 10
}

function applyQuoteToForm(
  prev: ValuationFormValues,
  quote: SentimentValuationQuoteResponse,
  options: { full?: boolean; fallbackName?: string } = {},
): ValuationFormValues {
  const next: ValuationFormValues = {
    ...prev,
    symbol: quote.symbol,
    name: quote.name || options.fallbackName || prev.name || quote.symbol,
  }

  if (quote.market_cap_yi != null && Number.isFinite(quote.market_cap_yi)) {
    next.actualCapYi = round1(quote.market_cap_yi)
  }
  if (quote.implied_earnings_yi != null && Number.isFinite(quote.implied_earnings_yi)) {
    next.earningsYi = round1(quote.implied_earnings_yi)
  }

  if (options.full) {
    if (quote.suggested_pe != null) next.basePe = quote.suggested_pe
    if (quote.index_close != null) next.indexPoints = Math.round(quote.index_close)
    if (quote.suggested_sentiment_coef != null) next.sentimentCoef = quote.suggested_sentiment_coef
  } else {
    if (quote.index_close != null) next.indexPoints = Math.round(quote.index_close)
  }

  return next
}

function describeQuoteUpdate(quote: SentimentValuationQuoteResponse): string {
  const parts: string[] = []
  if (quote.market_cap_yi != null) parts.push(`市值 ${round1(quote.market_cap_yi)} 亿`)
  if (quote.implied_earnings_yi != null) parts.push(`盈利 ${round1(quote.implied_earnings_yi)} 亿`)
  return parts.length > 0 ? parts.join(' · ') : '未获取到市值/盈利'
}

const STORAGE_KEY = 'final-trade-sentiment-valuation-v1'

function loadPersistedForm(): ValuationFormValues | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw) as ValuationFormValues
  } catch {
    return null
  }
}

function savePersistedForm(values: ValuationFormValues) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(values))
  } catch {
    // ignore
  }
}

export function SentimentValuationPage() {
  const navigate = useNavigate()
  const [form, setForm] = useState<ValuationFormValues>(() => loadPersistedForm() ?? defaultFormValues())
  const [searchOptions, setSearchOptions] = useState<Array<{ value: string; label: string; item: StockSearchResult }>>([])

  const quoteMutation = useMutation({ mutationFn: getSentimentValuationQuote })

  useEffect(() => {
    savePersistedForm(form)
  }, [form])

  const breakdown = useMemo(() => calcTheoreticalCap(form), [form])
  const impliedSentiment = breakdown.impliedSentimentCoef ?? breakdown.sentimentCoef

  const pageAIContext = useMemo(
    () => ({
      page: 'sentiment_valuation' as const,
      title: buildPageTitle('sentiment_valuation', form.name || form.symbol || undefined),
      symbol: form.symbol || null,
      payload: {
        symbol: form.symbol,
        name: form.name,
        earnings_yi: form.earningsYi,
        growth_rate_pct: form.growthRatePct,
        growth_coef: breakdown.growthCoef,
        base_pe: form.basePe,
        index_points: form.indexPoints,
        index_coef: breakdown.indexCoef,
        sentiment_coef: form.sentimentCoef,
        actual_cap_yi: form.actualCapYi,
        theoretical_cap_yi: breakdown.theoreticalCapYi,
        implied_sentiment_coef: breakdown.impliedSentimentCoef,
        sentiment_label: sentimentLabel(impliedSentiment),
        formula: FORMULA_TEXT,
        market_quote: quoteMutation.data ?? null,
      },
    }),
    [breakdown, form, impliedSentiment, quoteMutation.data],
  )
  useRegisterPageAIContext(pageAIContext)

  const chartOption = useMemo(() => {
    const base = form.earningsYi * breakdown.growthCoef * breakdown.basePe * breakdown.indexCoef
    const items = [
      { name: '基本面底座', value: base },
      { name: '情绪溢价', value: Math.max(0, breakdown.theoreticalCapYi - base) },
    ]
    if (form.actualCapYi != null && form.actualCapYi > breakdown.theoreticalCapYi) {
      items.push({
        name: '超出测算',
        value: form.actualCapYi - breakdown.theoreticalCapYi,
      })
    }
    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: { name?: string; value?: number }) =>
          `${params.name ?? ''}: ${formatCapYi(Number(params.value))}`,
      },
      series: [
        {
          type: 'pie',
          radius: ['42%', '72%'],
          label: { formatter: '{b}\n{d}%' },
          data: items,
          color: ['#0f8b6f', '#b86f1c', '#c4473d'],
        },
      ],
    }
  }, [breakdown, form.actualCapYi, form.earningsYi])

  const presetColumns: ColumnsType<ValuationPreset> = [
    {
      title: '案例',
      dataIndex: 'name',
      render: (_, row) => (
        <Space orientation="vertical" size={0}>
          <Typography.Text strong>{row.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>{row.symbol}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '当期盈利(亿)',
      dataIndex: 'earningsYi',
      width: 110,
    },
    {
      title: '增速%',
      dataIndex: 'growthRatePct',
      width: 80,
      render: (value: number) => `${value >= 0 ? '+' : ''}${value}%`,
    },
    {
      title: 'PE',
      dataIndex: 'basePe',
      width: 70,
    },
    {
      title: '实际市值(亿)',
      dataIndex: 'actualCapYi',
      width: 120,
      render: (value: number) => (value > 0 ? value.toFixed(0) : '--'),
    },
    {
      title: '说明',
      dataIndex: 'note',
      ellipsis: true,
    },
    {
      title: '操作',
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => setForm(presetToFormValues(row))}>
          载入
        </Button>
      ),
    },
  ]

  const updateForm = (patch: Partial<ValuationFormValues>) => {
    setForm((prev) => ({ ...prev, ...patch }))
  }

  const handleSearch = async (keyword: string) => {
    const trimmed = keyword.trim()
    if (!trimmed) {
      setSearchOptions([])
      return
    }
    try {
      const rows = await searchStockLibrary(trimmed, 12)
      setSearchOptions(
        rows.map((item) => ({
          value: item.symbol,
          label: `${item.name} (${item.ts_code})`,
          item,
        })),
      )
    } catch {
      setSearchOptions([])
    }
  }

  const handleSelectStock = async (symbol: string) => {
    const option = searchOptions.find((item) => item.value === symbol)
    updateForm({
      symbol,
      name: option?.item.name ?? symbol,
    })
    try {
      const quote = await quoteMutation.mutateAsync(symbol)
      setForm((prev) => applyQuoteToForm(prev, quote, { full: true, fallbackName: option?.item.name }))
      if (quote.degraded) {
        message.warning(`行情部分失败 · ${describeQuoteUpdate(quote)}`)
      } else {
        message.success(`已载入 ${quote.name || symbol} · ${describeQuoteUpdate(quote)}`)
      }
    } catch {
      message.error('行情拉取失败，请检查后端连接')
    }
  }

  const handleRefreshQuote = async () => {
    if (!form.symbol) return
    try {
      const quote = await quoteMutation.mutateAsync(form.symbol)
      setForm((prev) => applyQuoteToForm(prev, quote, { fallbackName: form.name }))
      const updated = describeQuoteUpdate(quote)
      if (quote.degraded || (quote.market_cap_yi == null && quote.implied_earnings_yi == null)) {
        message.warning(`刷新不完整 · ${updated}`)
      } else if (quote.implied_earnings_yi == null) {
        message.warning(`已更新市值 · ${updated}（该股无有效 PE，盈利请手动填写）`)
      } else {
        message.success(`已刷新 · ${updated}`)
      }
    } catch {
      message.error('行情拉取失败，请检查后端连接')
    }
  }

  return (
    <div className={styles.sentimentValuationPage}>
      <PageHeader
        title="A股情绪估值"
        subtitle="基于「市值 = 当期盈利 × 复合增速 × 基准PE × 大盘水位 × 情绪溢价」理解 A 股定价逻辑，测算理论市值与情绪溢价。"
        badge="研究工具"
      />

      <div className={styles.formulaBanner}>{FORMULA_TEXT}</div>

      <Collapse
        bordered={false}
        className="glass-card"
        items={[
          {
            key: 'theory',
            label: '模型说明（为何 DCF 在 A 股痛苦，以及四因子如何联动）',
            children: (
              <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                <Typography.Paragraph className={styles.disclaimer}>
                  A 股因涨跌停、缺乏做空与大量散户参与，普遍存在「情绪溢价」。同一 100 亿资金，若由 10 位基金经理缓慢建仓，往往走慢牛；若由百万散户在涨停制度下抢筹，则容易连板螺旋。
                </Typography.Paragraph>
                <Typography.Paragraph className={styles.disclaimer}>
                  <strong>当期盈利</strong>：今年预期净利润（亿）。<strong>基准 PE</strong>：传统行业 10–15、消费 15–20、半科技 20–25、科技 25–30、银行保险约 10、半导体/机器人等稀缺赛道可到 30。
                  <strong>复合增速系数</strong>：基准 1.0，+30% 取 1.3，-15% 取 0.85。<strong>大盘水位</strong>：3000 点 = 1.0，4090 点 ≈ 1.36。
                  <strong>情绪溢价</strong>：由关注度、涨停频率、热门榜等驱动，妖股极值约 5–6。
                </Typography.Paragraph>
                <Typography.Paragraph className={styles.disclaimer}>
                  产品涨价是最佳模式：同时抬升当期盈利、未来增速预期与情绪。靠业绩驱动的标的回调相对浅；纯情绪推动的标的波动大、高位难站稳。
                </Typography.Paragraph>
                <Alert
                  type="warning"
                  showIcon
                  message="免责声明"
                  description="本页仅供学术研究与定价理解，不构成任何投资建议。案例个股均不代表推荐，请勿据此交易。"
                />
              </Space>
            ),
          },
        ]}
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={10}>
          <Card title="参数输入" variant="borderless" className="glass-card float-in">
            <Space orientation="vertical" size={16} style={{ width: '100%' }}>
              <AutoComplete
                style={{ width: '100%' }}
                options={searchOptions}
                placeholder="搜索股票代码/名称，自动拉取市值与 PE"
                onSearch={handleSearch}
                onSelect={handleSelectStock}
                value={form.name || form.symbol}
                onChange={(value) => {
                  if (!value) updateForm({ symbol: '', name: '' })
                }}
              />
              <Row gutter={12}>
                <Col span={12}>
                  <Typography.Text type="secondary">当期盈利（亿）</Typography.Text>
                  <InputNumber
                    style={{ width: '100%', marginTop: 6 }}
                    min={0}
                    step={0.1}
                    value={form.earningsYi}
                    onChange={(value) => updateForm({ earningsYi: Number(value ?? 0) })}
                  />
                </Col>
                <Col span={12}>
                  <Typography.Text type="secondary">实际市值（亿，可选）</Typography.Text>
                  <InputNumber
                    style={{ width: '100%', marginTop: 6 }}
                    min={0}
                    step={1}
                    value={form.actualCapYi ?? undefined}
                    onChange={(value) => updateForm({ actualCapYi: value == null ? null : Number(value) })}
                  />
                </Col>
              </Row>

              <div>
                <Typography.Text type="secondary">
                  未来十年复合增速：{form.growthRatePct >= 0 ? '+' : ''}{form.growthRatePct}%（系数 {formatCoef(breakdown.growthCoef)}）
                </Typography.Text>
                <Slider
                  min={-40}
                  max={200}
                  step={1}
                  value={form.growthRatePct}
                  onChange={(value) => updateForm({ growthRatePct: value })}
                  marks={{ '-30': '-30%', 0: '0%', 30: '30%', 100: '100%' }}
                />
              </div>

              <div>
                <Typography.Text type="secondary">基准 PE：{form.basePe}x</Typography.Text>
                <Slider
                  min={8}
                  max={35}
                  step={0.5}
                  value={form.basePe}
                  onChange={(value) => updateForm({ basePe: value })}
                />
                <Space wrap size={[6, 6]}>
                  {INDUSTRY_PE_OPTIONS.map((item) => (
                    <Tag
                      key={item.label}
                      color={Math.abs(form.basePe - item.value) < 0.01 ? 'green' : 'default'}
                      style={{ cursor: 'pointer' }}
                      onClick={() => updateForm({ basePe: item.value })}
                    >
                      {item.label}
                    </Tag>
                  ))}
                </Space>
              </div>

              <div>
                <Typography.Text type="secondary">
                  大盘点位：{form.indexPoints}（系数 {formatCoef(breakdown.indexCoef, 3)}）
                </Typography.Text>
                <Slider
                  min={2400}
                  max={4500}
                  step={10}
                  value={form.indexPoints}
                  onChange={(value) => updateForm({ indexPoints: value })}
                  marks={{ 2700: '2700', 3000: '3000', 3300: '3300', 4090: '4090' }}
                />
              </div>

              <div>
                <Typography.Text type="secondary">
                  情绪溢价系数：{formatCoef(form.sentimentCoef)}（{sentimentLabel(form.sentimentCoef)}）
                </Typography.Text>
                <Slider
                  min={0.8}
                  max={6}
                  step={0.05}
                  value={form.sentimentCoef}
                  onChange={(value) => updateForm({ sentimentCoef: value })}
                  marks={{ 1: '1.0', 2: '2.0', 3.5: '3.5', 6: '6.0' }}
                />
              </div>

              <Space wrap>
                <Button
                  loading={quoteMutation.isPending}
                  disabled={!form.symbol}
                  onClick={handleRefreshQuote}
                >
                  刷新行情
                </Button>
                {form.symbol ? (
                  <Button type="link" onClick={() => navigate(`/stocks/${form.symbol}/chart`)}>
                    查看 K 线
                  </Button>
                ) : null}
              </Space>
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={14}>
          <Card title="测算结果" variant="borderless" className="glass-card float-in">
            <div className={styles.resultHero}>
              <Typography.Text type="secondary">理论市值</Typography.Text>
              <p className={styles.resultHeroValue}>{formatCapYi(breakdown.theoreticalCapYi)}</p>
              <p className={styles.resultHeroSub}>
                {form.name || form.symbol || '未选择标的'}
                {form.actualCapYi != null ? ` · 实际 ${formatCapYi(form.actualCapYi)}` : ''}
                {breakdown.gapPct != null ? ` · 偏差 ${breakdown.gapPct >= 0 ? '+' : ''}${breakdown.gapPct.toFixed(1)}%` : ''}
              </p>
            </div>

            <div className={styles.factorGrid} style={{ marginTop: 16 }}>
              {[
                { label: '当期盈利', value: `${form.earningsYi} 亿` },
                { label: '增速系数', value: formatCoef(breakdown.growthCoef) },
                { label: '基准 PE', value: `${breakdown.basePe}x` },
                { label: '大盘系数', value: formatCoef(breakdown.indexCoef, 3) },
                {
                  label: '隐含情绪',
                  value: breakdown.impliedSentimentCoef != null ? formatCoef(breakdown.impliedSentimentCoef) : '--',
                },
              ].map((item) => (
                <div key={item.label} className={styles.factorCard}>
                  <span className={styles.factorLabel}>{item.label}</span>
                  <span className={styles.factorValue}>{item.value}</span>
                </div>
              ))}
            </div>

            <Row gutter={[16, 16]} style={{ marginTop: 18 }}>
              <Col xs={24} md={12}>
                <ReactECharts option={chartOption} style={{ height: 260 }} />
              </Col>
              <Col xs={24} md={12}>
                <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                  <Statistic
                    title="情绪溢价标签"
                    value={sentimentLabel(impliedSentiment)}
                    valueStyle={{ color: sentimentColor(impliedSentiment), fontSize: 20 }}
                  />
                  {quoteMutation.data ? (
                    <>
                      <Typography.Text type="secondary">
                        近 60 日涨停 {quoteMutation.data.limit_up_count_60d} 次 · 大涨日 {quoteMutation.data.big_gain_days_60d} 天
                      </Typography.Text>
                      <Typography.Text type="secondary">
                        行业：{quoteMutation.data.industry || '--'} · 建议 PE {quoteMutation.data.suggested_pe ?? '--'}x
                      </Typography.Text>
                    </>
                  ) : null}
                  <Typography.Paragraph className={styles.presetNote}>
                    若实际市值高于理论值，差额主要来自情绪溢价；若低于理论值，可能定价偏悲观或业绩预期下修。
                  </Typography.Paragraph>
                </Space>
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>

      <Card title="经典案例（点击载入）" variant="borderless" className="glass-card float-in">
        <Table
          rowKey="key"
          size="small"
          pagination={false}
          columns={presetColumns}
          dataSource={VALUATION_PRESETS.map((preset) => {
            const presetBreakdown = calcTheoreticalCap(presetToFormValues(preset))
            const implied =
              preset.actualCapYi > 0
                ? calcTheoreticalCap({
                    ...presetToFormValues(preset),
                    sentimentCoef: 1,
                  }).theoreticalCapYi
                : null
            const impliedSent =
              implied && preset.actualCapYi > 0 ? preset.actualCapYi / implied : null
            return {
              ...preset,
              note:
                impliedSent != null
                  ? `${preset.note} · 隐含情绪 ${impliedSent.toFixed(2)}x · 理论 ${presetBreakdown.theoreticalCapYi.toFixed(0)} 亿`
                  : preset.note,
            }
          })}
        />
      </Card>
    </div>
  )
}
