import { useEffect, useMemo, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import { Link } from 'react-router-dom'
import {
  applyEventJudgmentProfile,
  deleteEventJudgmentProfile,
  getEventJudgmentProfiles,
  upsertEventJudgmentProfile,
} from '@/shared/api/endpoints'
import { PageHeader } from '@/shared/components/PageHeader'
import type { EventJudgmentDimension, EventJudgmentRuleOption, EventJudgmentRuleValue } from '@/types/contracts'

type Draft = {
  profile_id?: string
  source_score_mode: 'legacy_formula' | 'dimension_weighted'
  name: string
  description: string
  dimensions: EventJudgmentDimension[]
  rule_values: EventJudgmentRuleValue[]
}

type CompatListProps<T> = {
  dataSource: T[]
  renderItem: (item: T, index: number) => ReactNode
}

type CompatListItemProps = {
  actions?: ReactNode[]
  children: ReactNode
}

type CompatListComponent = {
  <T>(props: CompatListProps<T>): ReactElement
  Item: (props: CompatListItemProps) => ReactElement
}

const CompatList = (({ dataSource, renderItem }: CompatListProps<unknown>) => (
  <Space orientation="vertical" size={8} className="w-full">
    {dataSource.map((item, index) => (
      <div key={index}>
        {renderItem(item, index)}
      </div>
    ))}
  </Space>
)) as CompatListComponent

CompatList.Item = function CompatListItem({ actions, children }: CompatListItemProps) {
  return (
    <Card size="small" actions={actions}>
      {children}
    </Card>
  )
}

function normalizeBool(v: unknown, fallback: boolean): boolean {
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  if (typeof v === 'string') {
    const t = v.trim().toLowerCase()
    if (['1', 'true', 'yes', 'y', 'on'].includes(t)) return true
    if (['0', 'false', 'no', 'n', 'off'].includes(t)) return false
  }
  return fallback
}

function normalizeRuleValue(raw: unknown, opt: EventJudgmentRuleOption): number | boolean {
  if (opt.value_type === 'boolean') return normalizeBool(raw, normalizeBool(opt.default_value, false))
  let v = Number(raw ?? opt.default_value)
  if (!Number.isFinite(v)) v = Number(opt.default_value)
  if (typeof opt.min_value === 'number') v = Math.max(opt.min_value, v)
  if (typeof opt.max_value === 'number') v = Math.min(opt.max_value, v)
  return opt.value_type === 'integer' ? Math.round(v) : Number(v.toFixed(6))
}

function normalizeRuleValues(values: EventJudgmentRuleValue[] | undefined, opts: EventJudgmentRuleOption[]): EventJudgmentRuleValue[] {
  const m = new Map<string, number | boolean>()
  opts.forEach((o) => m.set(o.rule_key, normalizeRuleValue(o.default_value, o)))
  ;(values ?? []).forEach((r) => {
    const key = String(r.rule_key || '').trim()
    const opt = opts.find((o) => o.rule_key === key)
    if (!key || !opt) return
    m.set(key, normalizeRuleValue(r.value, opt))
  })
  return opts.map((o) => ({ rule_key: o.rule_key, value: m.get(o.rule_key) ?? o.default_value }))
}

function normalizeDims(input: EventJudgmentDimension[]): EventJudgmentDimension[] {
  const out: EventJudgmentDimension[] = []
  const seen = new Set<string>()
  input.forEach((d, i) => {
    const metric = String(d.metric_key || '').trim()
    if (!metric) return
    const id = String(d.dimension_id || '').trim() || `dim_${i + 1}`
    if (seen.has(id)) return
    seen.add(id)
    out.push({
      dimension_id: id.slice(0, 64),
      label: (String(d.label || '').trim() || metric).slice(0, 64),
      metric_key: metric,
      weight: Math.max(0, Math.min(10, Number(d.weight || 1))),
      invert: Boolean(d.invert),
      enabled: Boolean(d.enabled),
    })
  })
  return out.slice(0, 24)
}

function defaultDims(metricKeys: string[]): EventJudgmentDimension[] {
  const picked = (metricKeys.length > 0 ? metricKeys : ['event_background_score', 'event_position_score', 'event_vol_price_score']).slice(0, 3)
  const w = picked.length > 0 ? 1 / picked.length : 1
  return picked.map((k, i) => ({
    dimension_id: `dim_${i + 1}`,
    label: i === 0 ? '背景分' : i === 1 ? '位置分' : '量价分',
    metric_key: k,
    weight: w,
    invert: false,
    enabled: true,
  }))
}

function recRange(opt: EventJudgmentRuleOption): { min?: number; max?: number } {
  if (opt.value_type === 'boolean') return {}
  if (typeof opt.recommended_min === 'number' || typeof opt.recommended_max === 'number') {
    return { min: opt.recommended_min ?? undefined, max: opt.recommended_max ?? undefined }
  }
  if (
    typeof opt.min_value === 'number'
    && typeof opt.max_value === 'number'
    && typeof opt.default_value === 'number'
    && opt.max_value > opt.min_value
  ) {
    const s = (opt.max_value - opt.min_value) * 0.3
    return {
      min: Math.max(opt.min_value, opt.default_value - s),
      max: Math.min(opt.max_value, opt.default_value + s),
    }
  }
  return {}
}

function fmtNum(v: number, opt: EventJudgmentRuleOption): string {
  if (opt.value_type === 'integer') return `${Math.round(v)}`
  const step = typeof opt.step === 'number' ? opt.step : 0.01
  if (step >= 0.1) return v.toFixed(2)
  if (step >= 0.01) return v.toFixed(3)
  return v.toFixed(4)
}

function changed(cur: number | boolean, base: number | boolean, opt: EventJudgmentRuleOption): boolean {
  if (opt.value_type === 'boolean') return normalizeBool(cur, false) !== normalizeBool(base, false)
  return Math.abs(Number(cur) - Number(base)) > 1e-9
}

export function EventJudgmentPage() {
  const { message } = AntdApp.useApp()
  const query = useQuery({ queryKey: ['event-judgment-profiles'], queryFn: getEventJudgmentProfiles, staleTime: 60_000 })
  const metricOptions = query.data?.metric_options ?? []
  const ruleOptions = query.data?.rule_options ?? []
  const profiles = query.data?.profiles ?? []
  const activeProfileId = String(query.data?.active_profile_id || '').trim()
  const metricKeys = useMemo(() => metricOptions.map((m) => m.metric_key), [metricOptions])
  const metricDescMap = useMemo(
    () => new Map(metricOptions.map((m) => [m.metric_key, String(m.description || '').trim()])),
    [metricOptions],
  )

  const [selectedProfileId, setSelectedProfileId] = useState('')
  const [saving, setSaving] = useState(false)
  const [applying, setApplying] = useState(false)
  const [ruleKeyword, setRuleKeyword] = useState('')
  const [ruleCategory, setRuleCategory] = useState<string>('all')
  const [ruleChangedOnly, setRuleChangedOnly] = useState(false)
  const [draft, setDraft] = useState<Draft>({
    source_score_mode: 'dimension_weighted',
    name: '',
    description: '',
    dimensions: defaultDims([]),
    rule_values: normalizeRuleValues([], []),
  })

  const selectedProfile = useMemo(
    () => profiles.find((p) => p.profile_id === selectedProfileId) ?? null,
    [profiles, selectedProfileId],
  )

  useEffect(() => {
    if (profiles.length <= 0) return
    if (selectedProfileId === '__new__') return
    if (selectedProfileId && profiles.some((p) => p.profile_id === selectedProfileId)) return
    setSelectedProfileId(activeProfileId || profiles[0].profile_id)
  }, [profiles, selectedProfileId, activeProfileId])

  useEffect(() => {
    if (!selectedProfile) return
    setDraft({
      profile_id: selectedProfile.is_system ? undefined : selectedProfile.profile_id,
      source_score_mode: selectedProfile.score_mode,
      name: selectedProfile.is_system ? `${selectedProfile.name}-副本` : selectedProfile.name,
      description: selectedProfile.description || '',
      dimensions: normalizeDims(selectedProfile.dimensions).length > 0 ? normalizeDims(selectedProfile.dimensions) : defaultDims(metricKeys),
      rule_values: normalizeRuleValues(selectedProfile.rule_values, ruleOptions),
    })
  }, [selectedProfile?.profile_id, ruleOptions, metricKeys])

  const curRuleMap = useMemo(() => new Map(draft.rule_values.map((r) => [r.rule_key, r.value])), [draft.rule_values])
  const baseRuleMap = useMemo(() => {
    const base = normalizeRuleValues(selectedProfile?.rule_values, ruleOptions)
    return new Map(base.map((r) => [r.rule_key, r.value]))
  }, [selectedProfile?.profile_id, ruleOptions])

  const grouped = useMemo(() => {
    const m = new Map<string, EventJudgmentRuleOption[]>()
    ruleOptions.forEach((o) => {
      const c = String(o.category || '').trim() || '其他'
      if (!m.has(c)) m.set(c, [])
      m.get(c)?.push(o)
    })
    return Array.from(m.entries()).map(([category, options]) => ({ category, options }))
  }, [ruleOptions])

  const categoryOptions = useMemo(
    () => [{ label: '全部事件', value: 'all' }, ...grouped.map((g) => ({ label: g.category, value: g.category }))],
    [grouped],
  )

  const shownGroups = useMemo(() => {
    const k = ruleKeyword.trim().toLowerCase()
    return grouped
      .map((g) => ({
        ...g,
        options: g.options.filter((o) => {
          if (ruleCategory !== 'all' && g.category !== ruleCategory) return false
          const cur = curRuleMap.get(o.rule_key) ?? o.default_value
          const base = baseRuleMap.get(o.rule_key) ?? o.default_value
          if (ruleChangedOnly && !changed(cur, base, o)) return false
          if (!k) return true
          return [o.label, o.rule_key, o.description, o.category].join(' ').toLowerCase().includes(k)
        }),
      }))
      .filter((g) => g.options.length > 0)
  }, [grouped, ruleCategory, ruleKeyword, ruleChangedOnly, curRuleMap, baseRuleMap])

  const totalRules = useMemo(() => grouped.reduce((n, g) => n + g.options.length, 0), [grouped])
  const shownRules = useMemo(() => shownGroups.reduce((n, g) => n + g.options.length, 0), [shownGroups])

  function updateRule(ruleKey: string, value: number | boolean) {
    const key = String(ruleKey || '').trim()
    if (!key) return
    setDraft((prev) => {
      const next = [...prev.rule_values]
      const idx = next.findIndex((i) => i.rule_key === key)
      if (idx >= 0) next[idx] = { ...next[idx], value }
      else next.push({ rule_key: key, value })
      return { ...prev, rule_values: normalizeRuleValues(next, ruleOptions) }
    })
  }

  async function saveDraft() {
    const name = String(draft.name || '').trim()
    if (!name) return message.warning('请填写模板名称。')
    const dims = normalizeDims(draft.dimensions)
    if (dims.length <= 0) return message.warning('至少需要一个有效维度。')
    const rules = normalizeRuleValues(draft.rule_values, ruleOptions)
    setSaving(true)
    try {
      const saved = await upsertEventJudgmentProfile({
        profile_id: draft.profile_id,
        name,
        description: String(draft.description || '').trim(),
        dimensions: dims,
        rule_values: rules,
        make_active: true,
      })
      message.success('已保存并激活模板。')
      const res = await query.refetch()
      if ((res.data?.profiles ?? []).some((p) => p.profile_id === saved.profile_id)) setSelectedProfileId(saved.profile_id)
    } catch (e) {
      message.error((e as Error)?.message || '保存失败，请重试。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Space orientation="vertical" size={16} className="w-full">
      <PageHeader title="事件判别中心" subtitle="统一维护维科夫事件评分维度与触发规则阈值。" badge="核心逻辑" />
      <Alert type="info" showIcon title="执行说明" description={<Space wrap size={8}><Typography.Text type="secondary">当前激活模板会同时影响待买信号与策略回测。</Typography.Text><Link to="/signals">待买信号</Link><Link to="/backtest">策略回测</Link><Link to="/strategy">策略中心</Link></Space>} />

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card title="模板列表" extra={<Button onClick={() => {
            setSelectedProfileId('__new__')
            setRuleKeyword('')
            setRuleCategory('all')
            setRuleChangedOnly(false)
            setDraft({
              profile_id: undefined,
              source_score_mode: 'dimension_weighted',
              name: '自定义事件判别模板',
              description: '',
              dimensions: defaultDims(metricKeys),
              rule_values: normalizeRuleValues([], ruleOptions),
            })
          }}>新建自定义</Button>}>
            {profiles.length <= 0 ? <Empty description="暂无模板" /> : (
              <CompatList dataSource={profiles} renderItem={(item) => {
                const active = item.profile_id === activeProfileId
                const selected = item.profile_id === selectedProfileId
                return (
                  <CompatList.Item actions={[
                    <Button key="select" size="small" type={selected ? 'primary' : 'default'} ghost={selected} onClick={() => setSelectedProfileId(item.profile_id)}>{selected ? '已选中' : '选择'}</Button>,
                    <Button key="apply" size="small" loading={applying && active} disabled={active} onClick={async () => {
                      setApplying(true)
                      try {
                        await applyEventJudgmentProfile({ profile_id: item.profile_id })
                        await query.refetch()
                        message.success('已应用模板。')
                      } finally {
                        setApplying(false)
                      }
                    }}>{active ? '当前生效' : '应用'}</Button>,
                    !item.is_system ? <Popconfirm key="delete" title="确认删除该模板？" onConfirm={async () => {
                      await deleteEventJudgmentProfile(item.profile_id)
                      await query.refetch()
                      message.success('已删除模板。')
                    }}><Button size="small" danger>删除</Button></Popconfirm> : <span key="sys" />,
                  ]}>
                    <Space orientation="vertical" size={2} className="w-full">
                      <Space wrap size={6}>
                        <Typography.Text strong>{item.name}</Typography.Text>
                        {item.is_system ? <Tag color="blue">系统</Tag> : <Tag color="green">自定义</Tag>}
                        {active ? <Tag color="processing">生效中</Tag> : null}
                      </Space>
                      <Typography.Text type="secondary">维度数：{item.dimensions.length} | 规则数：{item.rule_values.length}</Typography.Text>
                    </Space>
                  </CompatList.Item>
                )
              }} />
            )}
          </Card>
        </Col>

        <Col xs={24} xl={16}>
          <Card title="模板编辑" extra={<Space><Button type="primary" loading={saving} onClick={() => { void saveDraft() }}>保存并激活</Button></Space>}>
            {draft.source_score_mode === 'legacy_formula' ? <Alert type="warning" showIcon title="当前模板为经典公式模式" description="保存时会转换为可编辑的维度加权模板。" className="mb-12" /> : null}

            <Row gutter={[12, 12]}>
              <Col xs={24} md={12}><Typography.Text type="secondary">模板名称</Typography.Text><Input value={draft.name} onChange={(e) => setDraft((p) => ({ ...p, name: e.target.value }))} /></Col>
              <Col xs={24} md={12}><Typography.Text type="secondary">模板说明</Typography.Text><Input value={draft.description} onChange={(e) => setDraft((p) => ({ ...p, description: e.target.value }))} /></Col>
            </Row>

            <Divider>评分维度</Divider>
            <Alert
              type="info"
              showIcon
              className="mb-12"
              message="维度分计算说明"
              description="总分 = Σ(有效分 × 权重) / Σ权重；反向=开启后有效分按 100-原始分计算；启用=关闭后该维度不参与计算。"
            />
            {draft.dimensions.length <= 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前草稿还没有维度" /> : (
              <Space orientation="vertical" size={10} className="w-full">
                <Card size="small">
                  <Row gutter={[8, 8]} align="middle">
                    <Col xs={24} md={5}><Typography.Text type="secondary">维度名称</Typography.Text></Col>
                    <Col xs={24} md={8}><Typography.Text type="secondary">评分指标</Typography.Text></Col>
                    <Col xs={12} md={3}><Typography.Text type="secondary">权重</Typography.Text></Col>
                    <Col xs={12} md={3}><Typography.Text type="secondary">反向</Typography.Text></Col>
                    <Col xs={12} md={3}><Typography.Text type="secondary">启用</Typography.Text></Col>
                    <Col xs={12} md={2}><Typography.Text type="secondary">操作</Typography.Text></Col>
                  </Row>
                </Card>
                {draft.dimensions.map((d, i) => (
                  <Card key={`${d.dimension_id}-${i}`} size="small">
                    <Row gutter={[8, 8]} align="middle">
                      <Col xs={24} md={5}><Input value={d.label} onChange={(e) => setDraft((p) => { const x = [...p.dimensions]; x[i] = { ...x[i], label: e.target.value }; return { ...p, dimensions: x } })} /></Col>
                      <Col xs={24} md={8}>
                        <Select className="w-full" value={d.metric_key} options={metricOptions.map((m) => ({ value: m.metric_key, label: `${m.label} (${m.metric_key})` }))} onChange={(v) => setDraft((p) => { const x = [...p.dimensions]; x[i] = { ...x[i], metric_key: String(v) }; return { ...p, dimensions: x } })} />
                        <Typography.Text type="secondary" className="text-muted">
                          {metricDescMap.get(d.metric_key) || '该指标暂无说明。'}
                        </Typography.Text>
                      </Col>
                      <Col xs={12} md={3}><InputNumber className="w-full" min={0} max={10} step={0.05} value={d.weight} onChange={(v) => setDraft((p) => { const x = [...p.dimensions]; x[i] = { ...x[i], weight: Number(v ?? 0) }; return { ...p, dimensions: x } })} /></Col>
                      <Col xs={12} md={3}><Switch checked={d.invert} checkedChildren="反" unCheckedChildren="正" onChange={(v) => setDraft((p) => { const x = [...p.dimensions]; x[i] = { ...x[i], invert: v }; return { ...p, dimensions: x } })} /></Col>
                      <Col xs={12} md={3}><Switch checked={d.enabled} checkedChildren="开" unCheckedChildren="关" onChange={(v) => setDraft((p) => { const x = [...p.dimensions]; x[i] = { ...x[i], enabled: v }; return { ...p, dimensions: x } })} /></Col>
                      <Col xs={12} md={2}><Button danger onClick={() => setDraft((p) => ({ ...p, dimensions: p.dimensions.filter((_, j) => j !== i) }))}>删除</Button></Col>
                    </Row>
                  </Card>
                ))}
                <Button onClick={() => setDraft((p) => ({ ...p, dimensions: [...p.dimensions, { dimension_id: `dim_${p.dimensions.length + 1}`, label: '新维度', metric_key: metricKeys[0] || 'event_background_score', weight: 1, invert: false, enabled: true }].slice(0, 24) }))}>新增维度</Button>
              </Space>
            )}

            <Divider>事件触发规则</Divider>
            {ruleOptions.length <= 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无规则配置项" /> : (
              <Space orientation="vertical" size={12} className="w-full">
                <Card size="small">
                  <Row gutter={[8, 8]}>
                    <Col xs={24} md={8}><Typography.Text type="secondary">按事件筛选</Typography.Text><Select className="w-full" value={ruleCategory} options={categoryOptions} onChange={(v) => setRuleCategory(String(v))} /></Col>
                    <Col xs={24} md={8}><Typography.Text type="secondary">关键词搜索</Typography.Text><Input value={ruleKeyword} placeholder="搜索规则名/key/说明" onChange={(e) => setRuleKeyword(e.target.value)} /></Col>
                    <Col xs={24} md={8}><Typography.Text type="secondary">显示选项</Typography.Text><div className="flex-align-center-gap"><Switch checked={ruleChangedOnly} onChange={setRuleChangedOnly} /><Typography.Text>只看改动项</Typography.Text><Typography.Text type="secondary">{shownRules}/{totalRules}</Typography.Text></div></Col>
                  </Row>
                </Card>

                {shownGroups.length <= 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的规则项" /> : null}
                {shownGroups.map((g) => (
                  <Card key={g.category} size="small" title={g.category}>
                    <Space orientation="vertical" size={8} className="w-full">
                      {g.options.map((o) => {
                        const cur = curRuleMap.get(o.rule_key) ?? o.default_value
                        const base = baseRuleMap.get(o.rule_key) ?? o.default_value
                        const isChanged = changed(cur, base, o)
                        const range = recRange(o)
                        const risk = o.value_type === 'boolean' ? { status: 'ok' as const, text: '' } : (
                          typeof range.min === 'number' && Number(cur) < range.min
                            ? { status: 'low' as const, text: o.risk_hint_low || '低于推荐区间，可能造成规则过松或失真。' }
                            : typeof range.max === 'number' && Number(cur) > range.max
                              ? { status: 'high' as const, text: o.risk_hint_high || '高于推荐区间，可能造成规则过严或漏判。' }
                              : { status: 'ok' as const, text: '当前值在推荐区间内。' }
                        )
                        return (
                          <Row key={o.rule_key} gutter={[8, 8]} align="middle">
                            <Col xs={24} md={11}>
                              <Space wrap size={6}>
                                <Typography.Text>{o.label}</Typography.Text>
                                {isChanged ? <Tag color="processing">已修改</Tag> : <Tag>默认/基线</Tag>}
                                {o.value_type !== 'boolean' ? (risk.status === 'ok' ? <Tag color="success">推荐区间内</Tag> : <Tag color="warning">{risk.status === 'low' ? '低于推荐' : '高于推荐'}</Tag>) : null}
                              </Space>
                              <br />
                              <Typography.Text type="secondary">{o.description || o.rule_key}</Typography.Text>
                              {o.value_type !== 'boolean' ? (
                                <>
                                  <br />
                                  <Typography.Text type="secondary">推荐区间：{typeof range.min === 'number' || typeof range.max === 'number' ? `${typeof range.min === 'number' ? fmtNum(range.min, o) : '-'} ~ ${typeof range.max === 'number' ? fmtNum(range.max, o) : '-'}` : '未配置'}</Typography.Text>
                                  <br />
                                  <Typography.Text type={risk.status === 'ok' ? 'secondary' : 'warning'}>风险提示：{risk.text}</Typography.Text>
                                </>
                              ) : null}
                            </Col>
                            <Col xs={24} md={7}>
                              <Typography.Text type="secondary">{o.rule_key}</Typography.Text>
                              <br />
                              <Typography.Text type="secondary">基线值：{o.value_type === 'boolean' ? (normalizeBool(base, normalizeBool(o.default_value, false)) ? '开启' : '关闭') : fmtNum(Number(base), o)}</Typography.Text>
                            </Col>
                            <Col xs={24} md={6}>
                              {o.value_type === 'boolean' ? (
                                <Switch checked={normalizeBool(cur, normalizeBool(o.default_value, false))} onChange={(v) => updateRule(o.rule_key, v)} />
                              ) : (
                                <InputNumber
                                  className="w-full"
                                  min={typeof o.min_value === 'number' ? o.min_value : undefined}
                                  max={typeof o.max_value === 'number' ? o.max_value : undefined}
                                  step={typeof o.step === 'number' ? o.step : (o.value_type === 'integer' ? 1 : 0.01)}
                                  value={Number(cur)}
                                  onChange={(v) => updateRule(o.rule_key, normalizeRuleValue(v ?? o.default_value, o))}
                                />
                              )}
                            </Col>
                          </Row>
                        )
                      })}
                    </Space>
                  </Card>
                ))}

                <Button onClick={() => setDraft((p) => ({ ...p, rule_values: normalizeRuleValues([], ruleOptions) }))}>规则恢复默认</Button>
              </Space>
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  )
}
