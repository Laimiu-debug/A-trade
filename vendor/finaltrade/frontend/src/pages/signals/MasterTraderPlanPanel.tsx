import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd'
import { LineChartOutlined, ReloadOutlined, ShoppingCartOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { apiRequest } from '@/shared/api/client'
import { upsertPendingBuyDraft } from '@/shared/utils/simPendingOrders'
import { MASTER_TRADER_STRATEGY_META } from '@/shared/utils/strategyParams'
import type { SignalScanMode, SignalsResponse, StrategyId, TrendPoolStep } from '@/types/contracts'

interface CustomHoldingItem {
  code: string
  name: string
  quantity: number
  averageCost: number
}

interface MasterTraderPlanPanelProps {
  strategyId: StrategyId
  strategyParams: Record<string, unknown>
  mode: SignalScanMode
  runId?: string
  trendStep: TrendPoolStep
  asOfDate?: string
  windowDays: number
  minScore: number
  refreshToken: number
  signalsData?: SignalsResponse
  signalsLoading?: boolean
  onForceRefreshSignals?: () => void
}

const STAGE_TAG_COLOR: Record<string, string> = {
  advance: 'red',
  trial: 'gold',
  chop: 'purple',
  decline: 'green',
}

function fmtPct(v: unknown, digits = 2, signed = false): string {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || !Number.isFinite(n)) return '—'
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

function fmtMoney(v: unknown): string {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || !Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return `¥${n.toFixed(2)}`
}

export function MasterTraderPlanPanel({
  strategyId,
  strategyParams,
  mode,
  runId,
  trendStep,
  asOfDate,
  windowDays,
  minScore,
  refreshToken,
  signalsData,
  signalsLoading = false,
  onForceRefreshSignals,
}: MasterTraderPlanPanelProps) {
  const meta = MASTER_TRADER_STRATEGY_META[String(strategyId || '').trim()]
  const navigate = useNavigate()
  const { message } = AntdApp.useApp()

  const [section, setSection] = useState<'all' | 'holdings' | 'newpos' | 'orders'>('all')
  const [useCustomHoldings, setUseCustomHoldings] = useState(false)
  const [customEquity, setCustomEquity] = useState<number>(100000)
  const [customHoldings, setCustomHoldings] = useState<CustomHoldingItem[]>([])
  const [draftCode, setDraftCode] = useState('')
  const [draftName, setDraftName] = useState('')
  const [draftQty, setDraftQty] = useState<number>(1000)
  const [draftCost, setDraftCost] = useState<number | null>(null)
  const [maxPositionsOverride, setMaxPositionsOverride] = useState<number | null>(null)
  const [stopLossOverride, setStopLossOverride] = useState<number | null>(null)
  const [manualTick, setManualTick] = useState(0)

  const sharedSignalsPayload = useMemo(() => {
    if (!signalsData || String(signalsData.strategy_id || '').trim() !== String(strategyId || '').trim()) {
      return null
    }
    const items = Array.isArray(signalsData.items) ? signalsData.items : []
    let activeItems = items.filter((it) => Number(it.signal_age_days ?? 0) <= 2)
    if (activeItems.length === 0 && items.length > 0) {
      activeItems = items.slice(0, 16)
    }
    const candidates = activeItems.slice(0, 20).map((it) => ({
      symbol: it.symbol,
      name: it.name,
      score: Number(it.entry_quality_score ?? 70),
      signalLabel: it.wyckoff_signal || it.primary_signal,
      reason: it.phase_hint || it.trigger_reason,
    }))
    return {
      candidates,
      signalsAsOf: signalsData.as_of_date || asOfDate || undefined,
      sourceCount: Number(signalsData.source_count ?? items.length),
      fingerprint: `${signalsData.as_of_date ?? ''}|${signalsData.source_count ?? 0}|${candidates.map((c) => `${c.symbol}:${c.score}`).join(',')}`,
    }
  }, [asOfDate, signalsData, strategyId])

  const planQuery = useQuery({
    queryKey: [
      'atrade-master-plan',
      strategyId,
      JSON.stringify(strategyParams),
      mode,
      runId ?? '',
      trendStep,
      asOfDate ?? '',
      windowDays,
      minScore,
      useCustomHoldings,
      customEquity,
      JSON.stringify(customHoldings),
      maxPositionsOverride ?? 'default',
      stopLossOverride ?? 'default',
      sharedSignalsPayload?.fingerprint ?? 'standalone',
      refreshToken,
      manualTick,
    ],
    enabled: Boolean(meta) && !signalsLoading,
    retry: false,
    staleTime: 20_000,
    queryFn: async () => {
      return apiRequest<any>('/api/atrade/master-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        timeoutMs: mode === 'full_market' ? 300_000 : 60_000,
        body: JSON.stringify({
          strategy_id: strategyId,
          strategy_params: strategyParams,
          mode,
          run_id: runId || undefined,
          trend_step: trendStep,
          as_of_date: asOfDate || undefined,
          window_days: windowDays,
          min_score: minScore,
          refresh: manualTick > 0 && !sharedSignalsPayload,
          provided_candidates: sharedSignalsPayload ? sharedSignalsPayload.candidates : undefined,
          signals_as_of: sharedSignalsPayload?.signalsAsOf,
          source_count: sharedSignalsPayload?.sourceCount,
          use_custom_holdings: useCustomHoldings,
          custom_positions: useCustomHoldings ? customHoldings : undefined,
          custom_equity: useCustomHoldings ? customEquity : undefined,
          max_positions: maxPositionsOverride ?? undefined,
          stop_loss_pct: stopLossOverride !== null ? -Math.abs(stopLossOverride) : undefined,
        }),
      })
    },
  })

  if (!meta) return null

  const data = planQuery.data
  const snap = data?.marketSnapshot ?? {}
  const dir = data?.directive ?? {}
  const lim = data?.activeLimits ?? {}
  const projSum = data?.projectedSummary ?? {}
  const timeline: any[] = data?.timelineChecklist ?? []
  const holdings: any[] = data?.holdingActions ?? []
  const npp = data?.newPositionPlan ?? {}
  const candPlaybooks: any[] = npp?.candidatePlaybooks ?? []
  const orders: any[] = data?.predictedOrders ?? []
  const projHoldings: any[] = data?.projectedHoldings ?? []
  const blocked: any[] = data?.blockedSignals ?? []
  const signalDate = String(snap?.date || asOfDate || new Date().toISOString().slice(0, 10))

  function handleAddCustomHolding() {
    const code = draftCode.replace(/\D/g, '').slice(0, 6)
    if (code.length !== 6 || !draftQty || draftQty <= 0 || !draftCost || draftCost <= 0) {
      message.warning('请填写6位股票代码、持仓股数与成本均价')
      return
    }
    setCustomHoldings((prev) => [
      ...prev.filter((item) => item.code !== code),
      { code, name: draftName.trim() || code, quantity: Math.round(draftQty / 100) * 100, averageCost: draftCost },
    ])
    setDraftCode('')
    setDraftName('')
    setDraftCost(null)
  }

  function handleLoadDemoHoldings() {
    setUseCustomHoldings(true)
    setCustomHoldings([
      { code: '002086', name: '东方海洋', quantity: 5000, averageCost: 4.2 },
      { code: '688981', name: '中芯国际', quantity: 200, averageCost: 92.0 },
      { code: '600900', name: '长江电力', quantity: 1000, averageCost: 27.5 },
    ])
  }

  function handleAddCandidateToPlan(item: any) {
    const sym = String(item?.symbol || '').trim().toLowerCase()
    if (!sym) return
    const qty = Math.max(100, Number(item?.plannedQty || 1000))
    upsertPendingBuyDraft({
      symbol: sym,
      name: String(item?.name || sym),
      signal_date: signalDate,
      source: 'signals',
      reference_price: Number(item?.close || item?.refPrice || 0) || undefined,
      default_quantity: qty,
    })
    window.dispatchEvent(new Event('atrade-plans-changed'))
    message.success(`已加入交易计划：${item?.name || sym} (${qty}股)`)
  }

  function handleBatchOrdersToPlan() {
    const buyOrders = orders.filter((o) => o.side === 'buy' && o.symbol)
    if (!buyOrders.length) {
      message.info('当前预测委托中暂无新买入委托')
      return
    }
    for (const o of buyOrders) {
      upsertPendingBuyDraft({
        symbol: String(o.symbol).toLowerCase(),
        name: String(o.name || o.code || o.symbol),
        signal_date: signalDate,
        source: 'signals',
        reference_price: Number(o.refPrice || 0) || undefined,
        default_quantity: Math.max(100, Number(o.quantity || 1000)),
      })
    }
    window.dispatchEvent(new Event('atrade-plans-changed'))
    message.success(`已将 ${buyOrders.length} 笔预测买入委托加入交易计划`)
  }

  return (
    <Card
      data-research="master-plan-panel"
      className="glass-card"
      variant="borderless"
      title={
        <Space wrap size={8}>
          <Tag color="volcano">大佬实战模式</Tag>
          <span>{`${meta.masterName} · 明日实战推演与持仓预测`}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            （与下方策略运行信号及「策略回测」共用同一套信号参数、本地通达信日线与风控引擎）
          </Typography.Text>
        </Space>
      }
      extra={
        <Space wrap size={8}>
          <Select
            aria-label="推演持仓来源"
            size="small"
            style={{ width: 145 }}
            value={useCustomHoldings ? 'custom' : 'account'}
            onChange={(val) => setUseCustomHoldings(val === 'custom')}
            options={[
              { value: 'account', label: '当前实盘账户持仓' },
              { value: 'custom', label: '手动演练持仓' },
            ]}
          />
          <Select
            aria-label="推演持仓上限"
            size="small"
            style={{ width: 132 }}
            value={maxPositionsOverride ?? 0}
            onChange={(val) => setMaxPositionsOverride(val === 0 ? null : Number(val))}
            options={[
              { value: 0, label: `默认上限(${meta.maxPositions}只)` },
              ...Array.from({ length: 20 }, (_, i) => ({ value: i + 1, label: `上限 ${i + 1} 只` })),
            ]}
          />
          <InputNumber
            aria-label="推演止损百分比"
            size="small"
            style={{ width: 118 }}
            min={1.5}
            max={20}
            step={0.5}
            prefix="止损"
            suffix="%"
            placeholder={`${meta.stopLossPercent}`}
            value={stopLossOverride ?? undefined}
            onChange={(v) => setStopLossOverride(typeof v === 'number' && Number.isFinite(v) ? v : null)}
          />
          <Button
            data-research="master-plan-refresh"
            size="small"
            type="primary"
            icon={<ReloadOutlined />}
            loading={signalsLoading || planQuery.isFetching}
            onClick={() => {
              setManualTick((v) => v + 1)
              onForceRefreshSignals?.()
            }}
          >
            生成/刷新明日预测
          </Button>
          <Button
            data-research="master-plan-goto-backtest"
            size="small"
            icon={<LineChartOutlined />}
            onClick={() => navigate(`/backtest?strategy_id=${encodeURIComponent(strategyId)}`, {
              state: {
                masterPlan: {
                  strategy_id: strategyId,
                  strategy_params: strategyParams,
                  mode,
                  run_id: mode === 'trend_pool' ? runId ?? '' : '',
                  trend_step: trendStep ?? 'auto',
                  window_days: windowDays,
                  min_score: minScore,
                  max_positions: maxPositionsOverride ?? meta.maxPositions,
                  stop_loss: (stopLossOverride ?? meta.stopLossPercent) / 100,
                  position_pct: meta.positionPctPercent / 100,
                  take_profit: meta.takeProfitPercent / 100,
                  max_hold_days: meta.maxHoldDays,
                },
              },
            })}
          >
            同参数去策略回测
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size={14} style={{ width: '100%' }}>
        {useCustomHoldings ? (
          <Card size="small" style={{ background: '#f8fafc' }}>
            <Space orientation="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
                <Typography.Text strong>手动演练持仓（不影响实盘账户账本，录入即重算明日四场景方案）</Typography.Text>
                <Space size={6}>
                  <Button size="small" onClick={handleLoadDemoHoldings}>填入3只演示持仓</Button>
                  <Button size="small" onClick={() => setCustomHoldings([])}>清空（按空仓推演）</Button>
                </Space>
              </Space>
              <Space wrap>
                <span>总资金基准：</span>
                <InputNumber
                  size="small"
                  min={10000}
                  step={10000}
                  value={customEquity}
                  onChange={(v) => { if (typeof v === 'number' && v >= 10000) setCustomEquity(v) }}
                />
                <Input
                  size="small"
                  style={{ width: 115 }}
                  placeholder="6位代码 如002086"
                  value={draftCode}
                  onChange={(e) => setDraftCode(e.target.value)}
                />
                <Input
                  size="small"
                  style={{ width: 110 }}
                  placeholder="名称(可选)"
                  value={draftName}
                  onChange={(e) => setDraftName(e.target.value)}
                />
                <InputNumber
                  size="small"
                  style={{ width: 105 }}
                  min={100}
                  step={100}
                  placeholder="股数"
                  value={draftQty}
                  onChange={(v) => { if (typeof v === 'number') setDraftQty(v) }}
                />
                <InputNumber
                  size="small"
                  style={{ width: 110 }}
                  min={0.01}
                  step={0.1}
                  placeholder="成本价"
                  value={draftCost ?? undefined}
                  onChange={(v) => setDraftCost(typeof v === 'number' ? v : null)}
                />
                <Button size="small" type="primary" onClick={handleAddCustomHolding}>添加持仓</Button>
              </Space>
              {customHoldings.length > 0 ? (
                <Space wrap size={6}>
                  {customHoldings.map((h) => (
                    <Tag
                      key={h.code}
                      closable
                      onClose={() => setCustomHoldings((prev) => prev.filter((item) => item.code !== h.code))}
                    >
                      {`${h.name}(${h.code}) · ${h.quantity}股 @ ¥${h.averageCost}`}
                    </Tag>
                  ))}
                </Space>
              ) : null}
            </Space>
          </Card>
        ) : null}

        {(signalsLoading || planQuery.isLoading) && !data ? (
          <Alert
            type="info"
            showIcon
            icon={<Spin size="small" />}
            title="正在生成明日实战推演与持仓预测…"
            description={
              mode === 'full_market'
                ? '正在同步全市场通达信日线信号扫描结果并推演明日持仓/开仓四场景预案，请稍候。'
                : '正在结合同源信号与账户持仓计算明日作战计划，请稍候。'
            }
          />
        ) : null}

        {!signalsLoading && planQuery.error ? (
          <Alert
            type="warning"
            showIcon
            title="明日实战预测暂未就绪"
            description={(planQuery.error as Error).message || '请确认已在行情设置连接本地通达信日线目录后点击刷新。'}
          />
        ) : null}

        {data ? (
          <>
            <Row gutter={[12, 12]}>
              <Col xs={24} sm={12} lg={6}>
                <Card size="small">
                  <Statistic
                    title={`今日市场环境 (${snap.date || '--'})`}
                    value={snap.stageLabel || '震荡'}
                    suffix={<Tag color={STAGE_TAG_COLOR[snap.stage] || 'blue'} style={{ marginLeft: 6 }}>{`额 ${snap.turnoverYi ? Math.round(snap.turnoverYi) + '亿' : '--'}`}</Tag>}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {`上证 ${fmtPct(snap.shChangePct, 2, true)} / 创业板 ${fmtPct(snap.cybChangePct, 2, true)}`}
                  </Typography.Text>
                </Card>
              </Col>
              <Col xs={24} sm={12} lg={6}>
                <Card size="small">
                  <Statistic
                    title="明日总仓 / 单票红线"
                    value={`${dir.recommendedTotalPos || '--'} / ≤${dir.maxSinglePosPct ?? '--'}%`}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {`最多 ${dir.maxHoldings ?? '--'} 只 · 首仓 ${fmtPct(lim.firstEntryPct, 1)} · 止损 ${fmtPct(lim.stopLossPct, 1)}`}
                  </Typography.Text>
                </Card>
              </Col>
              <Col xs={24} sm={12} lg={6}>
                <Card size="small">
                  <Statistic
                    title="预计明日仓位变化"
                    value={`${fmtPct(dir.currentPositionPct, 1)} → ${fmtPct(projSum.afterPosPct, 1)}`}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {`持股 ${projSum.beforeCount ?? 0} → ${projSum.afterCount ?? 0} 只 · 预测委托 ${orders.length} 笔`}
                  </Typography.Text>
                </Card>
              </Col>
              <Col xs={24} sm={12} lg={6}>
                <Card size="small">
                  <Statistic
                    title={data.accountSource === 'custom' ? '演练资金基准' : `账户基准${data.accountName ? ` (${data.accountName})` : ''}`}
                    value={fmtMoney(dir.accountEquityBasis)}
                  />
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {`可用现金 ${fmtMoney(dir.availableCashBasis)} · ${data.candidatePoolLabel || ''}`}
                  </Typography.Text>
                </Card>
              </Col>
            </Row>

            <Alert
              type="info"
              showIcon
              title={`明日总纲：${dir.positionVerdict || ''}`}
              description={dir.specialAlert || meta.tagline}
            />

            {timeline.length > 0 ? (
              <div>
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                  明日分时段执行时间表（盘前竞价 → 早盘排雷 → 止盈开仓 → 尾盘风控）
                </Typography.Text>
                <Row gutter={[10, 10]}>
                  {timeline.map((step: any) => (
                    <Col xs={24} sm={12} lg={6} key={step.window}>
                      <Card size="small" style={{ height: '100%', background: '#fcfdff' }}>
                        <Tag color="geekblue">{step.window}</Tag>
                        <div style={{ fontWeight: 600, marginTop: 4 }}>{step.phase}</div>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{step.action}</Typography.Text>
                      </Card>
                    </Col>
                  ))}
                </Row>
              </div>
            ) : null}

            <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
              <Radio.Group
                size="small"
                optionType="button"
                buttonStyle="solid"
                value={section}
                onChange={(e) => setSection(e.target.value)}
                options={[
                  { value: 'all', label: '全部展开' },
                  { value: 'holdings', label: `已有持仓四场景 (${holdings.length})` },
                  { value: 'newpos', label: `新开仓买法预案 (${candPlaybooks.length})` },
                  { value: 'orders', label: `明日预测委托单 (${orders.length})` },
                ]}
              />
              {orders.some((o) => o.side === 'buy') ? (
                <Button size="small" type="primary" ghost icon={<ShoppingCartOutlined />} onClick={handleBatchOrdersToPlan}>
                  一键将预测买单加入交易计划
                </Button>
              ) : null}
            </Space>

            {(section === 'all' || section === 'holdings') ? (
              <div>
                <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
                  {`一、对已有持仓怎么做（${holdings.length} 只）`}
                </Typography.Text>
                {holdings.length > 0 ? (
                  <Space orientation="vertical" size={10} style={{ width: '100%' }}>
                    {holdings.map((h: any) => {
                      const sp = h.scenarioPlaybook ?? {}
                      const bp = sp.basePlan ?? {}
                      const zp = sp.limitUpPlan ?? {}
                      const tp = sp.trimHoldPlan ?? {}
                      const cp = sp.breakdownPlan ?? {}
                      return (
                        <Card key={h.symbol || h.code} size="small">
                          <Space wrap style={{ justifyContent: 'space-between', width: '100%', marginBottom: 8 }}>
                            <Space wrap size={6}>
                              <Button
                                type="link"
                                style={{ padding: 0, fontWeight: 600 }}
                                onClick={() => navigate(`/stocks/${h.symbol}/chart?focus_date=${encodeURIComponent(signalDate)}`)}
                              >
                                {`${h.name} (${h.code})`}
                              </Button>
                              <Tag>{h.board}</Tag>
                              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                {`持 ${h.quantity}股 (${fmtPct(h.weightPct, 1)}) · 成本 ¥${Number(h.averageCost || 0).toFixed(2)} · 现价 ¥${Number(h.latestPrice || 0).toFixed(2)} (${fmtPct(h.unrealizedPnlPct, 2, true)})`}
                              </Typography.Text>
                              <Tag color="red">{`涨停 ¥${Number(h.limitUpPrice || 0).toFixed(2)}`}</Tag>
                              <Tag color="green">{`止损 ¥${Number(h.stopPrice || 0).toFixed(2)}`}</Tag>
                              <Tag>{`MA5 ¥${Number(h.ma5Price || 0).toFixed(2)}`}</Tag>
                            </Space>
                            <Tag color={h.urgency === 'high' ? 'error' : h.urgency === 'medium' ? 'warning' : 'processing'}>
                              {h.actionLabel}
                            </Tag>
                          </Space>
                          <Row gutter={[8, 8]}>
                            <Col xs={24} md={12} lg={6}>
                              <Card size="small" style={{ background: '#f8fafc', height: '100%' }}>
                                <div style={{ fontWeight: 600 }}>📌 明日基准指令 ({bp.window || h.executionWindow})</div>
                                <div style={{ fontSize: 12.5, marginTop: 4 }}>{bp.headline || h.targetPositionChange}</div>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{bp.detail || h.plainReason}</Typography.Text>
                              </Card>
                            </Col>
                            <Col xs={24} md={12} lg={6}>
                              <Card size="small" style={{ background: '#fff7f7', height: '100%' }}>
                                <div style={{ fontWeight: 600 }}>🔥 涨停怎么做 (¥{Number(h.limitUpPrice || 0).toFixed(2)})</div>
                                <div style={{ fontSize: 12.5, marginTop: 4 }}>{zp.headline}</div>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{zp.detail}</Typography.Text>
                              </Card>
                            </Col>
                            <Col xs={24} md={12} lg={6}>
                              <Card size="small" style={{ background: '#fffbeb', height: '100%' }}>
                                <div style={{ fontWeight: 600 }}>⚖️ 冲高不板 / 震荡怎么做</div>
                                <div style={{ fontSize: 12.5, marginTop: 4 }}>{tp.headline}</div>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{tp.detail}</Typography.Text>
                              </Card>
                            </Col>
                            <Col xs={24} md={12} lg={6}>
                              <Card size="small" style={{ background: '#f0fdf4', height: '100%' }}>
                                <div style={{ fontWeight: 600 }}>🛡️ 破位 / 走弱怎么做</div>
                                <div style={{ fontSize: 12.5, marginTop: 4 }}>{cp.headline}</div>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{cp.detail}</Typography.Text>
                              </Card>
                            </Col>
                          </Row>
                        </Card>
                      )
                    })}
                  </Space>
                ) : (
                  <Alert
                    type="success"
                    showIcon
                    title="当前为空仓（0 只持股，100% 现金）"
                    description={
                      <Space wrap>
                        <span>无旧仓排雷负担，可直接按下方同源策略信号择强建首仓；也可切换「手动演练持仓」测试任意持仓应对。</span>
                        <Button size="small" onClick={handleLoadDemoHoldings}>填入3只演示持仓体验</Button>
                      </Space>
                    }
                  />
                )}
              </div>
            ) : null}

            {(section === 'all' || section === 'newpos') ? (
              <div>
                <Space wrap style={{ justifyContent: 'space-between', width: '100%', marginBottom: 8 }}>
                  <Typography.Text strong>
                    {`二、如果要新开仓位怎么做 · 明日买法预案（可开槽位 ${npp.openSlots ?? 0}/${npp.maxSlots ?? '--'} 只 · 标准首仓 ${fmtPct(npp.firstEntryPct, 1)} ≈ ${fmtMoney(npp.perSlotBudget)}）`}
                  </Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>{npp.headline}</Typography.Text>
                </Space>
                {candPlaybooks.length > 0 ? (
                  <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                    {candPlaybooks.slice(0, 6).map((c: any) => (
                      <Card key={c.symbol || c.code} size="small">
                        <Space wrap style={{ justifyContent: 'space-between', width: '100%', marginBottom: 6 }}>
                          <Space wrap size={6}>
                            <Tag color={c.role === 'planned' ? 'red' : 'default'}>{`#${c.rank} ${c.roleLabel}`}</Tag>
                            <Button
                              type="link"
                              style={{ padding: 0, fontWeight: 600 }}
                              onClick={() => navigate(`/stocks/${c.symbol}/chart?focus_date=${encodeURIComponent(signalDate)}`)}
                            >
                              {`${c.name} (${c.code})`}
                            </Button>
                            <Tag>{c.board}</Tag>
                            {(c.matchTags ?? []).map((t: string) => <Tag key={t} color="blue">{t}</Tag>)}
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {`收 ¥${Number(c.close || 0).toFixed(2)} (${fmtPct(c.pct, 2, true)}) · 评分 ${Number(c.score || 0).toFixed(0)} · 首仓 ${c.plannedQty || 0}股 (${fmtMoney(c.plannedAmount)})`}
                            </Typography.Text>
                          </Space>
                          <Button size="small" type="primary" ghost icon={<ShoppingCartOutlined />} onClick={() => handleAddCandidateToPlan(c)}>
                            加入交易计划
                          </Button>
                        </Space>
                        <Row gutter={[8, 8]}>
                          <Col xs={24} md={8}><Typography.Text style={{ fontSize: 12 }}><b>✅ 明日怎么买：</b>{c.triggerZone}</Typography.Text></Col>
                          <Col xs={24} md={8}><Typography.Text style={{ fontSize: 12 }}><b>⛔ 坚决放弃条件：</b>{c.abortConditions}</Typography.Text></Col>
                          <Col xs={24} md={8}><Typography.Text style={{ fontSize: 12 }}><b>🔄 买入后预案：</b>{c.postBuyPlan}</Typography.Text></Col>
                        </Row>
                      </Card>
                    ))}
                  </Space>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="今日收盘同源扫描暂无满足该大佬硬门槛的新开仓标的，明日宁可空置槽位不强行出手。" />
                )}
              </div>
            ) : null}

            {(section === 'all' || section === 'orders') ? (
              <div>
                <Typography.Text strong style={{ display: 'block', marginBottom: 8 }}>
                  {`三、明日预测委托单汇总（共 ${orders.length} 笔 · 执行后预计持仓 ${projHoldings.length} 只，余现金 ${fmtPct(projSum.afterCashPct, 1)}）`}
                </Typography.Text>
                <Table
                  size="small"
                  rowKey={(row: any) => `${row.seq}-${row.symbol}-${row.actionType}`}
                  pagination={false}
                  dataSource={orders}
                  locale={{ emptyText: '按当前规则约束，明日无新增委托（锁仓观察或空仓防守）' }}
                  columns={[
                    { title: '序', dataIndex: 'seq', width: 52 },
                    {
                      title: '动作',
                      dataIndex: 'actionLabel',
                      width: 110,
                      render: (val: string, row: any) => <Tag color={row.side === 'buy' ? 'red' : 'green'}>{val}</Tag>,
                    },
                    {
                      title: '股票',
                      width: 150,
                      render: (_: unknown, row: any) => (
                        <Button
                          type="link"
                          size="small"
                          style={{ padding: 0 }}
                          onClick={() => navigate(`/stocks/${row.symbol}/chart?focus_date=${encodeURIComponent(signalDate)}`)}
                        >
                          {`${row.name || ''} ${row.code || ''}`}
                        </Button>
                      ),
                    },
                    {
                      title: '股数',
                      dataIndex: 'quantity',
                      width: 95,
                      render: (val: number, row: any) => `${row.side === 'buy' ? '+' : '-'}${val}`,
                    },
                    { title: '参考价', dataIndex: 'refPrice', width: 85, render: (v: number) => `¥${Number(v || 0).toFixed(2)}` },
                    { title: '预估额', dataIndex: 'estAmount', width: 100, render: (v: number) => fmtMoney(v) },
                    {
                      title: '单票仓位',
                      width: 120,
                      render: (_: unknown, row: any) => `${fmtPct(row.beforeWeightPct, 1)} → ${fmtPct(row.afterWeightPct, 1)}`,
                    },
                    { title: '触发硬规则与执行时段', render: (_: unknown, row: any) => `${row.reason || ''} (${row.execWindow || ''})` },
                  ]}
                />
                {blocked.length > 0 ? (
                  <Alert
                    style={{ marginTop: 8 }}
                    type="warning"
                    showIcon
                    title={`风控规则自动拦截 ${blocked.length} 项：${blocked.map((b: any) => `${b.name || b.code}(${b.reason})`).join('；')}`}
                  />
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </Space>
    </Card>
  )
}
