import { useEffect, useMemo, useState } from 'react'
import dayjs, { Dayjs } from 'dayjs'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { jsPDF } from 'jspdf'
import {
  App as AntdApp,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ApiError } from '@/shared/api/client'
import {
  createReviewTag,
  deleteDailyReview,
  deleteReviewTag,
  deleteWeeklyReview,
  getDailyReview,
  getDailyReviews,
  getReviewFillTags,
  getReviewTagStats,
  getReviewTags,
  getWeeklyReview,
  getWeeklyReviews,
  updateReviewFillTag,
  upsertDailyReview,
  upsertWeeklyReview,
} from '@/shared/api/endpoints'
import type {
  DailyReviewPayload,
  DailyReviewRecord,
  ReviewTagStatItem,
  ReviewTagType,
  SimTradeFill,
  TradeFillTagAssignment,
  WeeklyReviewPayload,
  WeeklyReviewRecord,
} from '@/types/contracts'
import { formatMoney } from '@/shared/utils/format'

type WorkspaceProps = {
  dateFrom: string
  dateTo: string
  fills: SimTradeFill[]
}

function formatApiError(error: unknown) {
  if (error instanceof ApiError) {
    return error.message || `请求失败: ${error.code}`
  }
  if (error instanceof Error) {
    return error.message || '请求失败'
  }
  return '请求失败'
}

function isNotFoundError(error: unknown) {
  if (!(error instanceof ApiError)) return false
  return error.code === 'REVIEW_DAILY_NOT_FOUND'
    || error.code === 'REVIEW_WEEKLY_NOT_FOUND'
    || error.code === 'NOT_FOUND'
    || error.code === 'HTTP_404'
}

function parseTags(value: string) {
  const tokens = value
    .split(/[\n,，]/g)
    .map((item) => item.trim())
    .filter(Boolean)
  return [...new Set(tokens)].slice(0, 20)
}

function bufferToBase64(buffer: ArrayBuffer) {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return btoa(binary)
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function buildDailyReviewMarkdown(record: DailyReviewRecord) {
  const lines = [
    `# 日复盘 - ${record.date}`,
    '',
    `- 更新时间: ${record.updated_at}`,
    `- 标签: ${(record.tags || []).join('、') || '-'}`,
    '',
    '## 标题',
    record.title || '-',
    '',
    '## 市场总结',
    record.market_summary || '-',
    '',
    '## 操作总结',
    record.operations_summary || '-',
    '',
    '## 反思',
    record.reflection || '-',
    '',
    '## 明日计划',
    record.tomorrow_plan || '-',
    '',
    '## 总结',
    record.summary || '-',
    '',
  ]
  return lines.join('\n')
}

function buildWeeklyReviewMarkdown(record: WeeklyReviewRecord) {
  const lines = [
    `# 周复盘 - ${record.week_label}`,
    '',
    `- 区间: ${record.start_date || '-'} ~ ${record.end_date || '-'}`,
    `- 更新时间: ${record.updated_at}`,
    `- 标签: ${(record.tags || []).join('、') || '-'}`,
    '',
    '## 核心目标回顾',
    record.core_goals || '-',
    '',
    '## 成果评估',
    record.achievements || '-',
    '',
    '## 资源投入分析',
    record.resource_analysis || '-',
    '',
    '## 市场节奏判断',
    record.market_rhythm || '-',
    '',
    '## 下周策略',
    record.next_week_strategy || '-',
    '',
    '## 关键认知',
    record.key_insight || '-',
    '',
  ]
  return lines.join('\n')
}

async function exportTextToPdf(title: string, text: string, filename: string) {
  const doc = new jsPDF({ unit: 'pt', format: 'a4' })
  let useCnFont = false
  try {
    const fontResp = await fetch('/fonts/LXGWWenKai-Regular.ttf')
    if (fontResp.ok) {
      const buffer = await fontResp.arrayBuffer()
      const base64 = bufferToBase64(buffer)
      doc.addFileToVFS('LXGWWenKai-Regular.ttf', base64)
      doc.addFont('LXGWWenKai-Regular.ttf', 'LXGWWenKai', 'normal')
      doc.setFont('LXGWWenKai', 'normal')
      useCnFont = true
    }
  } catch {
    useCnFont = false
  }

  const safeText = (value: string) => {
    if (useCnFont) return value
    return Array.from(value)
      .filter((char) => {
        const code = char.charCodeAt(0)
        return code >= 32 && code <= 126
      })
      .join('')
  }

  let y = 40
  doc.setFontSize(16)
  doc.text(safeText(title), 40, y)
  y += 24
  doc.setFontSize(11)

  const paragraphs = text.split('\n')
  for (const paragraph of paragraphs) {
    const wrapped = doc.splitTextToSize(safeText(paragraph || ' '), 515)
    if (y + wrapped.length * 14 > 790) {
      doc.addPage()
      y = 40
    }
    doc.text(wrapped, 40, y)
    y += wrapped.length * 14 + 4
  }

  doc.save(filename)
}

function getWeekLabelFromDate(date: Dayjs) {
  const source = date.toDate()
  const d = new Date(Date.UTC(source.getFullYear(), source.getMonth(), source.getDate()))
  const dayNum = d.getUTCDay() || 7
  d.setUTCDate(d.getUTCDate() + 4 - dayNum)
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1))
  const week = Math.ceil((((d.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

const EMPTY_DAILY: DailyReviewPayload = {
  title: '',
  market_summary: '',
  operations_summary: '',
  reflection: '',
  tomorrow_plan: '',
  summary: '',
  tags: [],
}

const EMPTY_WEEKLY: WeeklyReviewPayload = {
  start_date: '',
  end_date: '',
  core_goals: '',
  achievements: '',
  resource_analysis: '',
  market_rhythm: '',
  next_week_strategy: '',
  key_insight: '',
  tags: [],
}

export function ReviewWorkspacePanel({ dateFrom, dateTo, fills }: WorkspaceProps) {
  const { message } = AntdApp.useApp()
  const queryClient = useQueryClient()

  const [dailyDate, setDailyDate] = useState<Dayjs>(dayjs(dateTo))
  const [weeklyDate, setWeeklyDate] = useState<Dayjs>(dayjs(dateTo))
  const [dailyTagsText, setDailyTagsText] = useState('')
  const [weeklyTagsText, setWeeklyTagsText] = useState('')
  const [newEmotionTag, setNewEmotionTag] = useState('')
  const [newReasonTag, setNewReasonTag] = useState('')
  const [selectedFillOrderIds, setSelectedFillOrderIds] = useState<string[]>([])
  const [batchEmotionTagId, setBatchEmotionTagId] = useState<string | null>(null)
  const [batchReasonTagIds, setBatchReasonTagIds] = useState<string[]>([])
  const [dailyForm] = Form.useForm<DailyReviewPayload>()
  const [weeklyForm] = Form.useForm<WeeklyReviewPayload>()

  useEffect(() => {
    setDailyDate(dayjs(dateTo))
    setWeeklyDate(dayjs(dateTo))
  }, [dateTo])

  useEffect(() => {
    const allowed = new Set(fills.map((item) => item.order_id))
    setSelectedFillOrderIds((prev) => prev.filter((id) => allowed.has(id)))
  }, [fills])

  const dailyDateKey = dailyDate.format('YYYY-MM-DD')
  const weekLabel = getWeekLabelFromDate(weeklyDate)

  const dailyListQuery = useQuery({
    queryKey: ['review-daily', dateFrom, dateTo],
    queryFn: () => getDailyReviews({ date_from: dateFrom, date_to: dateTo }),
  })

  const weeklyListQuery = useQuery({
    queryKey: ['review-weekly', dayjs(dateTo).year()],
    queryFn: () => getWeeklyReviews({ year: dayjs(dateTo).year() }),
  })

  const dailyRecordQuery = useQuery({
    queryKey: ['review-daily-record', dailyDateKey],
    queryFn: async () => {
      try {
        return await getDailyReview(dailyDateKey)
      } catch (error) {
        if (isNotFoundError(error)) return null
        throw error
      }
    },
    retry: false,
  })

  const weeklyRecordQuery = useQuery({
    queryKey: ['review-weekly-record', weekLabel],
    queryFn: async () => {
      try {
        return await getWeeklyReview(weekLabel)
      } catch (error) {
        if (isNotFoundError(error)) return null
        throw error
      }
    },
    retry: false,
  })

  const reviewTagsQuery = useQuery({
    queryKey: ['review-tags'],
    queryFn: getReviewTags,
  })

  const fillTagsQuery = useQuery({
    queryKey: ['review-fill-tags'],
    queryFn: getReviewFillTags,
  })

  const tagStatsQuery = useQuery({
    queryKey: ['review-tag-stats', dateFrom, dateTo],
    queryFn: () => getReviewTagStats({ date_from: dateFrom, date_to: dateTo }),
  })

  const selectedDaily = useMemo(() => {
    if (dailyRecordQuery.data !== undefined) return dailyRecordQuery.data
    return dailyListQuery.data?.items.find((item) => item.date === dailyDateKey) ?? null
  }, [dailyDateKey, dailyListQuery.data?.items, dailyRecordQuery.data])

  const selectedWeekly = useMemo(() => {
    if (weeklyRecordQuery.data !== undefined) return weeklyRecordQuery.data
    return weeklyListQuery.data?.items.find((item) => item.week_label === weekLabel) ?? null
  }, [weekLabel, weeklyListQuery.data?.items, weeklyRecordQuery.data])

  async function openDailyRecord(targetDate: Dayjs, silent = false) {
    const targetDateKey = targetDate.format('YYYY-MM-DD')
    setDailyDate(targetDate)
    try {
      const row = await queryClient.fetchQuery({
        queryKey: ['review-daily-record', targetDateKey],
        queryFn: async () => {
          try {
            return await getDailyReview(targetDateKey)
          } catch (error) {
            if (isNotFoundError(error)) return null
            throw error
          }
        },
        staleTime: 0,
      })
      if (!silent) {
        if (row) message.success(`已打开: ${row.date}`)
        else message.warning('当前日期没有日复盘')
      }
    } catch (error) {
      message.error(formatApiError(error))
    }
  }

  async function openWeeklyRecord(targetDate: Dayjs, silent = false) {
    const targetWeekLabel = getWeekLabelFromDate(targetDate)
    setWeeklyDate(targetDate)
    try {
      const row = await queryClient.fetchQuery({
        queryKey: ['review-weekly-record', targetWeekLabel],
        queryFn: async () => {
          try {
            return await getWeeklyReview(targetWeekLabel)
          } catch (error) {
            if (isNotFoundError(error)) return null
            throw error
          }
        },
        staleTime: 0,
      })
      if (!silent) {
        if (row) message.success(`已打开: ${row.week_label}`)
        else message.warning('当前周没有周复盘')
      }
    } catch (error) {
      message.error(formatApiError(error))
    }
  }

  useEffect(() => {
    const value = selectedDaily ?? { ...EMPTY_DAILY, date: dailyDateKey, updated_at: '' }
    dailyForm.setFieldsValue({
      title: value.title,
      market_summary: value.market_summary,
      operations_summary: value.operations_summary,
      reflection: value.reflection,
      tomorrow_plan: value.tomorrow_plan,
      summary: value.summary,
      tags: value.tags,
    })
    setDailyTagsText((value.tags || []).join(', '))
  }, [dailyDateKey, dailyForm, selectedDaily])

  useEffect(() => {
    const value = selectedWeekly ?? { ...EMPTY_WEEKLY, week_label: weekLabel, updated_at: '' }
    weeklyForm.setFieldsValue({
      start_date: value.start_date,
      end_date: value.end_date,
      core_goals: value.core_goals,
      achievements: value.achievements,
      resource_analysis: value.resource_analysis,
      market_rhythm: value.market_rhythm,
      next_week_strategy: value.next_week_strategy,
      key_insight: value.key_insight,
      tags: value.tags,
    })
    setWeeklyTagsText((value.tags || []).join(', '))
  }, [weekLabel, selectedWeekly, weeklyForm])

  const fillTagMap = useMemo(() => {
    const map = new Map<string, TradeFillTagAssignment>()
    for (const row of fillTagsQuery.data ?? []) {
      map.set(row.order_id, row)
    }
    return map
  }, [fillTagsQuery.data])

  const saveDailyMutation = useMutation({
    mutationFn: async () => {
      const formValue = await dailyForm.validateFields()
      const payload: DailyReviewPayload = {
        ...formValue,
        tags: parseTags(dailyTagsText),
      }
      return upsertDailyReview(dailyDateKey, payload)
    },
    onSuccess: () => {
      message.success('日复盘已保存')
      queryClient.invalidateQueries({ queryKey: ['review-daily'] })
      queryClient.invalidateQueries({ queryKey: ['review-daily-record', dailyDateKey] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const deleteDailyMutation = useMutation({
    mutationFn: () => deleteDailyReview(dailyDateKey),
    onSuccess: () => {
      message.success('日复盘已删除')
      queryClient.invalidateQueries({ queryKey: ['review-daily'] })
      queryClient.invalidateQueries({ queryKey: ['review-daily-record', dailyDateKey] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const saveWeeklyMutation = useMutation({
    mutationFn: async () => {
      const formValue = await weeklyForm.validateFields()
      const payload: WeeklyReviewPayload = {
        ...formValue,
        tags: parseTags(weeklyTagsText),
      }
      return upsertWeeklyReview(weekLabel, payload)
    },
    onSuccess: () => {
      message.success('周复盘已保存')
      queryClient.invalidateQueries({ queryKey: ['review-weekly'] })
      queryClient.invalidateQueries({ queryKey: ['review-weekly-record', weekLabel] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const deleteWeeklyMutation = useMutation({
    mutationFn: () => deleteWeeklyReview(weekLabel),
    onSuccess: () => {
      message.success('周复盘已删除')
      queryClient.invalidateQueries({ queryKey: ['review-weekly'] })
      queryClient.invalidateQueries({ queryKey: ['review-weekly-record', weekLabel] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const createTagMutation = useMutation({
    mutationFn: ({ type, name }: { type: ReviewTagType; name: string }) => createReviewTag(type, { name }),
    onSuccess: (_, variables) => {
      if (variables.type === 'emotion') setNewEmotionTag('')
      if (variables.type === 'reason') setNewReasonTag('')
      message.success('标签已创建')
      queryClient.invalidateQueries({ queryKey: ['review-tags'] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const deleteTagMutation = useMutation({
    mutationFn: ({ type, id }: { type: ReviewTagType; id: string }) => deleteReviewTag(type, id),
    onSuccess: () => {
      message.success('标签已删除')
      queryClient.invalidateQueries({ queryKey: ['review-tags'] })
      queryClient.invalidateQueries({ queryKey: ['review-fill-tags'] })
      queryClient.invalidateQueries({ queryKey: ['review-tag-stats'] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const updateFillTagMutation = useMutation({
    mutationFn: ({ orderId, payload }: { orderId: string; payload: { emotion_tag_id?: string | null; reason_tag_ids: string[] } }) =>
      updateReviewFillTag(orderId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['review-fill-tags'] })
      queryClient.invalidateQueries({ queryKey: ['review-tag-stats'] })
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  const batchUpdateFillTagsMutation = useMutation({
    mutationFn: async (payload: { emotion_tag_id: string | null; reason_tag_ids: string[] }) => {
      if (selectedFillOrderIds.length === 0) {
        throw new Error('请先选择成交记录')
      }
      await Promise.all(
        selectedFillOrderIds.map((orderId) =>
          updateReviewFillTag(orderId, {
            emotion_tag_id: payload.emotion_tag_id,
            reason_tag_ids: payload.reason_tag_ids,
          }),
        ),
      )
      return selectedFillOrderIds.length
    },
    onSuccess: (count) => {
      message.success(`已批量更新 ${count} 条成交`)
      queryClient.invalidateQueries({ queryKey: ['review-fill-tags'] })
      queryClient.invalidateQueries({ queryKey: ['review-tag-stats'] })
      setSelectedFillOrderIds([])
    },
    onError: (error) => message.error(formatApiError(error)),
  })

  async function handleExportDailyMarkdown() {
    if (!selectedDaily) {
      message.warning('当前日期没有日复盘')
      return
    }
    const markdown = buildDailyReviewMarkdown(selectedDaily)
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    downloadBlob(`daily-review-${selectedDaily.date}.md`, blob)
    message.success('日复盘 Markdown 已导出')
  }

  async function handleExportDailyPdf() {
    if (!selectedDaily) {
      message.warning('当前日期没有日复盘')
      return
    }
    const markdown = buildDailyReviewMarkdown(selectedDaily)
    await exportTextToPdf(`日复盘 ${selectedDaily.date}`, markdown, `daily-review-${selectedDaily.date}.pdf`)
    message.success('日复盘 PDF 已导出')
  }

  async function handleExportWeeklyMarkdown() {
    if (!selectedWeekly) {
      message.warning('当前周没有周复盘')
      return
    }
    const markdown = buildWeeklyReviewMarkdown(selectedWeekly)
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
    downloadBlob(`weekly-review-${selectedWeekly.week_label}.md`, blob)
    message.success('周复盘 Markdown 已导出')
  }

  async function handleExportWeeklyPdf() {
    if (!selectedWeekly) {
      message.warning('当前周没有周复盘')
      return
    }
    const markdown = buildWeeklyReviewMarkdown(selectedWeekly)
    await exportTextToPdf(`周复盘 ${selectedWeekly.week_label}`, markdown, `weekly-review-${selectedWeekly.week_label}.pdf`)
    message.success('周复盘 PDF 已导出')
  }

  const dailyColumns: ColumnsType<DailyReviewRecord> = [
    { title: '日期', dataIndex: 'date', width: 120 },
    { title: '标题', dataIndex: 'title', width: 220 },
    { title: '更新时间', dataIndex: 'updated_at', width: 180 },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => void openDailyRecord(dayjs(row.date))}>
          打开
        </Button>
      ),
    },
  ]

  const weeklyColumns: ColumnsType<WeeklyReviewRecord> = [
    { title: '周', dataIndex: 'week_label', width: 120 },
    { title: '区间', key: 'range', width: 240, render: (_, row) => `${row.start_date || '-'} ~ ${row.end_date || '-'}` },
    { title: '关键认知', dataIndex: 'key_insight' },
    { title: '更新时间', dataIndex: 'updated_at', width: 180 },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) => (
        <Button
          type="link"
          size="small"
          onClick={() => void openWeeklyRecord(dayjs(row.start_date || row.end_date || dateTo))}
        >
          打开
        </Button>
      ),
    },
  ]

  const tagStatColumns: ColumnsType<ReviewTagStatItem> = [
    {
      title: '标签',
      key: 'name',
      width: 180,
      render: (_, row) => <Tag color={row.color}>{row.name}</Tag>,
    },
    { title: '次数', dataIndex: 'count', width: 100 },
    {
      title: '成交额',
      dataIndex: 'gross_amount',
      width: 160,
      render: (value: number) => formatMoney(value),
    },
    {
      title: '净现金',
      dataIndex: 'net_amount',
      width: 160,
      render: (value: number) => formatMoney(value),
    },
  ]

  const fillTagColumns: ColumnsType<SimTradeFill> = [
    { title: '订单号', dataIndex: 'order_id', width: 180 },
    { title: '代码', dataIndex: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', width: 80 },
    { title: '成交日', dataIndex: 'fill_date', width: 110 },
    {
      title: '净现金',
      dataIndex: 'net_amount',
      width: 130,
      render: (value: number) => formatMoney(value),
    },
    {
      title: '情绪标签',
      key: 'emotion',
      width: 200,
      render: (_, row) => {
        const current = fillTagMap.get(row.order_id)
        return (
          <Select
            allowClear
            size="small"
            style={{ width: '100%' }}
            placeholder="选择情绪"
            value={current?.emotion_tag_id ?? undefined}
            options={(reviewTagsQuery.data?.emotion ?? []).map((item) => ({
              label: item.name,
              value: item.id,
            }))}
            onChange={(value) => {
              updateFillTagMutation.mutate({
                orderId: row.order_id,
                payload: {
                  emotion_tag_id: value ?? null,
                  reason_tag_ids: current?.reason_tag_ids ?? [],
                },
              })
            }}
          />
        )
      },
    },
    {
      title: '原因标签',
      key: 'reason',
      width: 320,
      render: (_, row) => {
        const current = fillTagMap.get(row.order_id)
        return (
          <Select
            mode="multiple"
            allowClear
            maxTagCount={2}
            size="small"
            style={{ width: '100%' }}
            placeholder="选择原因"
            value={current?.reason_tag_ids ?? []}
            options={(reviewTagsQuery.data?.reason ?? []).map((item) => ({
              label: item.name,
              value: item.id,
            }))}
            onChange={(values) => {
              updateFillTagMutation.mutate({
                orderId: row.order_id,
                payload: {
                  emotion_tag_id: current?.emotion_tag_id ?? null,
                  reason_tag_ids: values,
                },
              })
            }}
          />
        )
      },
    },
  ]

  return (
    <Card className="glass-card" variant="borderless">
      <Space orientation="vertical" size={16} style={{ width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          复盘工作台
        </Typography.Title>
        <Typography.Text type="secondary">
          支持日复盘、周复盘与交易标签统计。股票搜索分享和资讯面板已独立到左侧导航。
        </Typography.Text>

        <Tabs
          defaultActiveKey="daily"
          items={[
            {
              key: 'daily',
              label: '日复盘',
              forceRender: true,
              children: (
                <Space orientation="vertical" size={16} style={{ width: '100%' }}>
                  <Space wrap>
                    <DatePicker value={dailyDate} onChange={(value) => value && setDailyDate(value)} />
                    <Button onClick={() => void openDailyRecord(dailyDate)} loading={dailyRecordQuery.isFetching}>
                      打开当前日期
                    </Button>
                    <Button type="primary" loading={saveDailyMutation.isPending} onClick={() => saveDailyMutation.mutate()}>
                      保存日复盘
                    </Button>
                    <Button onClick={() => void handleExportDailyMarkdown()}>导出 Markdown</Button>
                    <Button onClick={() => void handleExportDailyPdf()}>导出 PDF</Button>
                    <Popconfirm
                      title="确认删除该日复盘？"
                      onConfirm={() => deleteDailyMutation.mutate()}
                      okButtonProps={{ loading: deleteDailyMutation.isPending }}
                    >
                      <Button danger>删除</Button>
                    </Popconfirm>
                    <Typography.Text type={selectedDaily ? 'secondary' : 'warning'}>
                      {selectedDaily ? `已打开: ${selectedDaily.date}` : '当前日期暂无已保存复盘'}
                    </Typography.Text>
                    <Typography.Text type="secondary">更新时间: {selectedDaily?.updated_at || '-'}</Typography.Text>
                  </Space>

                  <Form layout="vertical" form={dailyForm}>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="标题" name="title">
                          <Input placeholder="例如：震荡市里的仓位管理" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="标签（逗号分隔）">
                          <Input
                            value={dailyTagsText}
                            onChange={(event) => setDailyTagsText(event.target.value)}
                            placeholder="纪律, 风控, 主线"
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="市场总结" name="market_summary">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="操作总结" name="operations_summary">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="反思" name="reflection">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="明日计划" name="tomorrow_plan">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item label="总结" name="summary">
                      <Input.TextArea rows={3} />
                    </Form.Item>
                  </Form>

                  <Table
                    rowKey={(row) => row.date}
                    columns={dailyColumns}
                    dataSource={dailyListQuery.data?.items ?? []}
                    loading={dailyListQuery.isLoading}
                    size="small"
                    pagination={{ pageSize: 8 }}
                    onRow={(record) => ({ onClick: () => void openDailyRecord(dayjs(record.date), true) })}
                  />
                </Space>
              ),
            },
            {
              key: 'weekly',
              label: '周复盘',
              forceRender: true,
              children: (
                <Space orientation="vertical" size={16} style={{ width: '100%' }}>
                  <Space wrap>
                    <DatePicker value={weeklyDate} onChange={(value) => value && setWeeklyDate(value)} />
                    <Typography.Text>周标识: {weekLabel}</Typography.Text>
                    <Button onClick={() => void openWeeklyRecord(weeklyDate)} loading={weeklyRecordQuery.isFetching}>
                      打开当前周
                    </Button>
                    <Button type="primary" loading={saveWeeklyMutation.isPending} onClick={() => saveWeeklyMutation.mutate()}>
                      保存周复盘
                    </Button>
                    <Button onClick={() => void handleExportWeeklyMarkdown()}>导出 Markdown</Button>
                    <Button onClick={() => void handleExportWeeklyPdf()}>导出 PDF</Button>
                    <Popconfirm
                      title="确认删除该周复盘？"
                      onConfirm={() => deleteWeeklyMutation.mutate()}
                      okButtonProps={{ loading: deleteWeeklyMutation.isPending }}
                    >
                      <Button danger>删除</Button>
                    </Popconfirm>
                    <Typography.Text type={selectedWeekly ? 'secondary' : 'warning'}>
                      {selectedWeekly ? `已打开: ${selectedWeekly.week_label}` : '当前周暂无已保存复盘'}
                    </Typography.Text>
                    <Typography.Text type="secondary">更新时间: {selectedWeekly?.updated_at || '-'}</Typography.Text>
                  </Space>

                  <Form layout="vertical" form={weeklyForm}>
                    <Row gutter={12}>
                      <Col xs={24} lg={8}>
                        <Form.Item label="开始日期" name="start_date">
                          <Input placeholder="留空可自动计算" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={8}>
                        <Form.Item label="结束日期" name="end_date">
                          <Input placeholder="留空可自动计算" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={8}>
                        <Form.Item label="标签（逗号分隔）">
                          <Input
                            value={weeklyTagsText}
                            onChange={(event) => setWeeklyTagsText(event.target.value)}
                            placeholder="主线, 仓位, 风险"
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="核心目标回顾" name="core_goals">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="成果评估" name="achievements">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="资源投入分析" name="resource_analysis">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="市场节奏判断" name="market_rhythm">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} lg={12}>
                        <Form.Item label="下周策略" name="next_week_strategy">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} lg={12}>
                        <Form.Item label="关键认知" name="key_insight">
                          <Input.TextArea rows={4} />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Form>

                  <Table
                    rowKey={(row) => row.week_label}
                    columns={weeklyColumns}
                    dataSource={weeklyListQuery.data?.items ?? []}
                    loading={weeklyListQuery.isLoading}
                    size="small"
                    pagination={{ pageSize: 8 }}
                    onRow={(record) => ({ onClick: () => void openWeeklyRecord(dayjs(record.start_date || dateTo), true) })}
                  />
                </Space>
              ),
            },
            {
              key: 'tags',
              label: '交易标签与统计',
              children: (
                <Space orientation="vertical" size={16} style={{ width: '100%' }}>
                  <Row gutter={[12, 12]}>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="情绪标签">
                        <Space orientation="vertical" style={{ width: '100%' }}>
                          <Space.Compact style={{ width: '100%' }}>
                            <Input value={newEmotionTag} onChange={(event) => setNewEmotionTag(event.target.value)} placeholder="新增情绪标签" />
                            <Button
                              type="primary"
                              loading={createTagMutation.isPending}
                              onClick={() => {
                                const name = newEmotionTag.trim()
                                if (!name) return
                                createTagMutation.mutate({ type: 'emotion', name })
                              }}
                            >
                              添加
                            </Button>
                          </Space.Compact>
                          <Space wrap>
                            {(reviewTagsQuery.data?.emotion ?? []).map((item) => (
                              <Tag key={item.id} color={item.color}>
                                <Space size={4}>
                                  <span>{item.name}</span>
                                  <Popconfirm title="删除该标签？" onConfirm={() => deleteTagMutation.mutate({ type: 'emotion', id: item.id })}>
                                    <a>删除</a>
                                  </Popconfirm>
                                </Space>
                              </Tag>
                            ))}
                          </Space>
                        </Space>
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="原因标签">
                        <Space orientation="vertical" style={{ width: '100%' }}>
                          <Space.Compact style={{ width: '100%' }}>
                            <Input value={newReasonTag} onChange={(event) => setNewReasonTag(event.target.value)} placeholder="新增原因标签" />
                            <Button
                              type="primary"
                              loading={createTagMutation.isPending}
                              onClick={() => {
                                const name = newReasonTag.trim()
                                if (!name) return
                                createTagMutation.mutate({ type: 'reason', name })
                              }}
                            >
                              添加
                            </Button>
                          </Space.Compact>
                          <Space wrap>
                            {(reviewTagsQuery.data?.reason ?? []).map((item) => (
                              <Tag key={item.id} color={item.color}>
                                <Space size={4}>
                                  <span>{item.name}</span>
                                  <Popconfirm title="删除该标签？" onConfirm={() => deleteTagMutation.mutate({ type: 'reason', id: item.id })}>
                                    <a>删除</a>
                                  </Popconfirm>
                                </Space>
                              </Tag>
                            ))}
                          </Space>
                        </Space>
                      </Card>
                    </Col>
                  </Row>

                  <Card size="small" title="成交标签标注（支持批量）">
                    <Space orientation="vertical" size={12} style={{ width: '100%' }}>
                      <Space wrap>
                        <Typography.Text>已选 {selectedFillOrderIds.length} 条</Typography.Text>
                        <Select
                          allowClear
                          style={{ width: 220 }}
                          placeholder="批量情绪标签"
                          value={batchEmotionTagId ?? undefined}
                          options={(reviewTagsQuery.data?.emotion ?? []).map((item) => ({ label: item.name, value: item.id }))}
                          onChange={(value) => setBatchEmotionTagId(value ?? null)}
                        />
                        <Select
                          mode="multiple"
                          allowClear
                          maxTagCount={2}
                          style={{ minWidth: 320 }}
                          placeholder="批量原因标签"
                          value={batchReasonTagIds}
                          options={(reviewTagsQuery.data?.reason ?? []).map((item) => ({ label: item.name, value: item.id }))}
                          onChange={(value) => setBatchReasonTagIds(value)}
                        />
                        <Button
                          type="primary"
                          loading={batchUpdateFillTagsMutation.isPending}
                          disabled={selectedFillOrderIds.length === 0}
                          onClick={() => batchUpdateFillTagsMutation.mutate({ emotion_tag_id: batchEmotionTagId, reason_tag_ids: batchReasonTagIds })}
                        >
                          批量应用
                        </Button>
                        <Button
                          danger
                          loading={batchUpdateFillTagsMutation.isPending}
                          disabled={selectedFillOrderIds.length === 0}
                          onClick={() => batchUpdateFillTagsMutation.mutate({ emotion_tag_id: null, reason_tag_ids: [] })}
                        >
                          清空所选标签
                        </Button>
                      </Space>

                      <Table
                        rowKey={(row) => row.order_id}
                        columns={fillTagColumns}
                        dataSource={fills}
                        loading={fillTagsQuery.isLoading || reviewTagsQuery.isLoading || updateFillTagMutation.isPending}
                        size="small"
                        pagination={{ pageSize: 8, showSizeChanger: true }}
                        scroll={{ x: 1200 }}
                        rowSelection={{
                          selectedRowKeys: selectedFillOrderIds,
                          onChange: (keys) => setSelectedFillOrderIds(keys.map((item) => String(item))),
                        }}
                      />
                    </Space>
                  </Card>

                  <Row gutter={[12, 12]}>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="情绪标签统计">
                        <Table rowKey={(row) => row.tag_id} columns={tagStatColumns} dataSource={tagStatsQuery.data?.emotion ?? []} loading={tagStatsQuery.isLoading} size="small" pagination={false} />
                      </Card>
                    </Col>
                    <Col xs={24} lg={12}>
                      <Card size="small" title="原因标签统计">
                        <Table rowKey={(row) => row.tag_id} columns={tagStatColumns} dataSource={tagStatsQuery.data?.reason ?? []} loading={tagStatsQuery.isLoading} size="small" pagination={false} />
                      </Card>
                    </Col>
                  </Row>
                </Space>
              ),
            },
          ]}
        />
      </Space>
    </Card>
  )
}
