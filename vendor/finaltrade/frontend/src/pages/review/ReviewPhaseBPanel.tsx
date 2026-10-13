import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { App as AntdApp, Alert, Button, Card, Col, Input, Row, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useNavigate } from 'react-router-dom'
import { getAIRecords, getStockAnalysis, getStockCandles } from '@/shared/api/endpoints'
import { formatPct } from '@/shared/utils/format'
import type { CandlePoint, StockAnnotation } from '@/types/contracts'
import { getStockLibraryStats, searchStockLibrary, type StockSearchResult } from '@/shared/services/stockLibrary'

type ReviewPhaseBPanelProps = {
  dateFrom: string
  dateTo: string
}

type ReviewSharePanelState = {
  keyword: string
  selectedTsCode: string
  note: string
  historyItems: StockSearchResult[]
}

type PriceSnapshot = {
  latest: number | null
  prev: number | null
  change: number | null
  changePct: number | null
  trend20Pct: number | null
  high20: number | null
  low20: number | null
  sparkline: number[]
}

const REVIEW_SHARE_PANEL_STORAGE_KEY = 'final-trade-review-share-panel-v1'

function loadReviewSharePanelState(): ReviewSharePanelState {
  if (typeof window === 'undefined') {
    return { keyword: '', selectedTsCode: '', note: '', historyItems: [] }
  }
  try {
    const raw = window.localStorage.getItem(REVIEW_SHARE_PANEL_STORAGE_KEY)
    if (!raw) return { keyword: '', selectedTsCode: '', note: '', historyItems: [] }
    const parsed = JSON.parse(raw) as Partial<ReviewSharePanelState>
    const historyItems = Array.isArray(parsed.historyItems)
      ? parsed.historyItems.filter((item): item is StockSearchResult => (
        Boolean(item)
        && typeof item.ts_code === 'string'
        && typeof item.name === 'string'
        && typeof item.symbol === 'string'
        && typeof item.industry === 'string'
        && typeof item.market === 'string'
        && typeof item.cnspell === 'string'
        && typeof item.exchange === 'string'
        && typeof item.list_status === 'string'
      ))
      : []
    return {
      keyword: typeof parsed.keyword === 'string' ? parsed.keyword : '',
      selectedTsCode: typeof parsed.selectedTsCode === 'string' ? parsed.selectedTsCode : '',
      note: typeof parsed.note === 'string' ? parsed.note : '',
      historyItems,
    }
  } catch {
    return { keyword: '', selectedTsCode: '', note: '', historyItems: [] }
  }
}

function saveReviewSharePanelState(state: ReviewSharePanelState) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(REVIEW_SHARE_PANEL_STORAGE_KEY, JSON.stringify(state))
  } catch {
    // ignore localStorage errors
  }
}

function tsCodeToPrefixedSymbol(tsCode: string) {
  const [code, suffix] = tsCode.toUpperCase().split('.')
  if (!code || !suffix) return ''
  if (suffix === 'SH') return `sh${code}`
  if (suffix === 'SZ') return `sz${code}`
  if (suffix === 'BJ') return `bj${code}`
  return ''
}

function downloadBlob(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

function formatPrice(value: number | null) {
  if (value === null || Number.isNaN(value)) return '--'
  return value.toFixed(2)
}

function formatSignedPrice(value: number | null) {
  if (value === null || Number.isNaN(value)) return '--'
  if (value > 0) return `+${value.toFixed(2)}`
  return value.toFixed(2)
}

function formatSignedPct(value: number | null) {
  if (value === null || Number.isNaN(value)) return '--'
  return value > 0 ? `+${(value * 100).toFixed(2)}%` : `${(value * 100).toFixed(2)}%`
}

function shortenText(value: string, max = 72) {
  const text = value.trim()
  if (!text) return '-'
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function calculatePriceSnapshot(candles: CandlePoint[]): PriceSnapshot {
  if (candles.length === 0) {
    return {
      latest: null,
      prev: null,
      change: null,
      changePct: null,
      trend20Pct: null,
      high20: null,
      low20: null,
      sparkline: [],
    }
  }
  const latest = candles[candles.length - 1].close
  const prev = candles.length > 1 ? candles[candles.length - 2].close : null
  const recent20 = candles.slice(-20)
  const trendStart = recent20[0]?.close ?? latest
  const change = prev && prev > 0 ? latest - prev : null
  const changePct = prev && prev > 0 ? (latest - prev) / prev : null
  const trend20Pct = trendStart > 0 ? (latest - trendStart) / trendStart : null
  const high20 = recent20.length > 0 ? Math.max(...recent20.map((item) => item.high)) : null
  const low20 = recent20.length > 0 ? Math.min(...recent20.map((item) => item.low)) : null
  return {
    latest,
    prev,
    change,
    changePct,
    trend20Pct,
    high20,
    low20,
    sparkline: candles.slice(-30).map((item) => item.close),
  }
}

function trendLabel(value: number | null) {
  if (value === null || Number.isNaN(value)) return '未知'
  if (value >= 0.08) return '上升'
  if (value <= -0.08) return '下降'
  return '震荡'
}

function stageLabel(stage: StockAnnotation['stage']) {
  if (stage === 'Early') return '发酵中'
  if (stage === 'Mid') return '高潮'
  return '退潮'
}

function formatConfidence(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function drawSparkline(ctx: CanvasRenderingContext2D, points: number[], x: number, y: number, width: number, height: number) {
  if (points.length < 2) return
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = Math.max(0.0001, max - min)

  ctx.save()
  ctx.beginPath()
  for (let i = 0; i < points.length; i += 1) {
    const px = x + (i / (points.length - 1)) * width
    const py = y + height - ((points[i] - min) / span) * height
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.strokeStyle = '#0f766e'
  ctx.lineWidth = 4
  ctx.stroke()
  ctx.restore()
}

function drawWrappedText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  maxLines: number,
) {
  if (!text.trim()) return
  const chars = Array.from(text)
  const lines: string[] = []
  let current = ''
  for (const char of chars) {
    const next = `${current}${char}`
    if (ctx.measureText(next).width > maxWidth && current) {
      lines.push(current)
      current = char
      if (lines.length >= maxLines) break
    } else {
      current = next
    }
  }
  if (lines.length < maxLines && current) {
    lines.push(current)
  }
  lines.slice(0, maxLines).forEach((line, index) => {
    ctx.fillText(line, x, y + index * lineHeight)
  })
}

export function ReviewPhaseBPanel({ dateFrom, dateTo }: ReviewPhaseBPanelProps) {
  const persistedState = useMemo(() => loadReviewSharePanelState(), [])
  const { message } = AntdApp.useApp()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState(persistedState.keyword)
  const [rows, setRows] = useState<StockSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<StockSearchResult | null>(null)
  const [note, setNote] = useState(persistedState.note)
  const [preferredTsCode, setPreferredTsCode] = useState(persistedState.selectedTsCode)
  const [historyItems, setHistoryItems] = useState<StockSearchResult[]>(persistedState.historyItems)

  const statsQuery = useQuery({
    queryKey: ['stock-library-stats'],
    queryFn: getStockLibraryStats,
  })

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(async () => {
      try {
        if (!keyword.trim()) {
          if (active) {
            setRows([])
            setLoading(false)
          }
          return
        }
        if (active) setLoading(true)
        const result = await searchStockLibrary(keyword.trim(), 30)
        if (active) setRows(result)
      } catch (error) {
        if (active) message.error(error instanceof Error ? error.message : '股票库加载失败')
      } finally {
        if (active) setLoading(false)
      }
    }, 180)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [keyword, message])

  useEffect(() => {
    if (!preferredTsCode || rows.length === 0 || selected?.ts_code === preferredTsCode) return
    const matched = rows.find((item) => item.ts_code === preferredTsCode)
    if (matched) {
      setSelected(matched)
    }
  }, [preferredTsCode, rows, selected?.ts_code])

  useEffect(() => {
    if (!selected && rows.length > 0) {
      setSelected(rows[0])
    }
  }, [rows, selected])

  useEffect(() => {
    saveReviewSharePanelState({
      keyword,
      selectedTsCode: selected?.ts_code || preferredTsCode || '',
      note,
      historyItems,
    })
  }, [historyItems, keyword, note, preferredTsCode, selected?.ts_code])

  function updateSelectionHistory(row: StockSearchResult) {
    setHistoryItems((previous) => {
      const deduped = previous.filter((item) => item.ts_code !== row.ts_code)
      return [row, ...deduped].slice(0, 12)
    })
  }

  function applySelection(row: StockSearchResult, ensureInRows = false) {
    setSelected(row)
    setPreferredTsCode(row.ts_code)
    updateSelectionHistory(row)
    if (ensureInRows) {
      setRows((previous) => {
        if (previous.some((item) => item.ts_code === row.ts_code)) return previous
        return [row, ...previous]
      })
    }
  }

  const selectedSymbol = useMemo(() => {
    if (!selected) return ''
    return tsCodeToPrefixedSymbol(selected.ts_code).toLowerCase()
  }, [selected])

  const candlesQuery = useQuery({
    queryKey: ['phase-b-share-card-candles', selectedSymbol],
    queryFn: () => getStockCandles(selectedSymbol),
    enabled: Boolean(selectedSymbol),
  })

  const stockAnalysisQuery = useQuery({
    queryKey: ['phase-b-share-card-analysis', selectedSymbol],
    queryFn: () => getStockAnalysis(selectedSymbol),
    enabled: Boolean(selectedSymbol),
  })

  const aiRecordsQuery = useQuery({
    queryKey: ['phase-b-share-card-ai-records'],
    queryFn: getAIRecords,
  })

  const priceSnapshot = useMemo(
    () => calculatePriceSnapshot(candlesQuery.data?.candles ?? []),
    [candlesQuery.data?.candles],
  )

  const klineAnnotation = useMemo(() => {
    const annotation = stockAnalysisQuery.data?.annotation
    if (!annotation) return null
    if (annotation.updated_by === 'manual') return annotation
    if (annotation.notes.trim().length > 0) return annotation
    return null
  }, [stockAnalysisQuery.data?.annotation])

  const latestAiRecord = useMemo(() => {
    if (!selectedSymbol) return null
    return (
      (aiRecordsQuery.data?.items ?? []).find(
        (item) => item.symbol.trim().toLowerCase() === selectedSymbol.trim().toLowerCase(),
      ) ?? null
    )
  }, [aiRecordsQuery.data?.items, selectedSymbol])

  const klineSummary = useMemo(() => {
    if (!klineAnnotation) return ''
    return `起始日 ${klineAnnotation.start_date} · 阶段 ${stageLabel(klineAnnotation.stage)} · 趋势 ${klineAnnotation.trend_class} · 决策 ${klineAnnotation.decision}`
  }, [klineAnnotation])

  const klineNotes = useMemo(() => {
    if (!klineAnnotation || !klineAnnotation.notes.trim()) return ''
    return shortenText(klineAnnotation.notes, 120)
  }, [klineAnnotation])

  const aiSummary = useMemo(() => {
    if (!latestAiRecord) return ''
    const parts = [
      latestAiRecord.provider || '--',
      latestAiRecord.conclusion || '--',
      `置信度 ${formatConfidence(latestAiRecord.confidence)}`,
      `起爆日 ${latestAiRecord.breakout_date || '--'}`,
    ]
    return parts.join(' · ')
  }, [latestAiRecord])

  const aiNotes = useMemo(() => {
    if (!latestAiRecord) return ''
    const core = latestAiRecord.summary.trim()
    if (core) return shortenText(core, 120)
    const parts = [
      `题材 ${latestAiRecord.theme_name || '--'}`,
      `趋势 ${latestAiRecord.trend_bull_type || '--'}`,
    ]
    return parts.join(' · ')
  }, [latestAiRecord])

  const cardInsights = useMemo(
    () =>
      [
        klineSummary ? { title: 'K线分析', content: klineSummary, maxLines: 2 } : null,
        klineNotes ? { title: 'K线备注', content: klineNotes, maxLines: 3 } : null,
        aiSummary ? { title: 'AI分析', content: aiSummary, maxLines: 2 } : null,
        aiNotes ? { title: 'AI摘要', content: aiNotes, maxLines: 3 } : null,
      ].filter((item): item is { title: string; content: string; maxLines: number } => Boolean(item)),
    [aiNotes, aiSummary, klineNotes, klineSummary],
  )

  const shareText = useMemo(() => {
    if (!selected) return ''
    return [
      `【复盘分享卡】${selected.name} (${selected.ts_code})`,
      `行业: ${selected.industry || '-'}`,
      `市场: ${selected.market || '-'}`,
      `复盘区间: ${dateFrom} ~ ${dateTo}`,
      `最新价: ${formatPrice(priceSnapshot.latest)} (${formatSignedPrice(priceSnapshot.change)} / ${formatSignedPct(priceSnapshot.changePct)})`,
      `20日走势: ${trendLabel(priceSnapshot.trend20Pct)} (${formatSignedPct(priceSnapshot.trend20Pct)})`,
      `20日区间: ${formatPrice(priceSnapshot.low20)} - ${formatPrice(priceSnapshot.high20)}`,
      klineSummary ? `K线分析: ${klineSummary}` : '',
      klineNotes ? `K线备注: ${klineNotes}` : '',
      aiSummary ? `AI分析: ${aiSummary}` : '',
      aiNotes ? `AI摘要: ${aiNotes}` : '',
      note.trim() ? `补充观点: ${shortenText(note, 88)}` : '',
    ]
      .filter(Boolean)
      .join('\n')
  }, [aiNotes, aiSummary, dateFrom, dateTo, klineNotes, klineSummary, note, priceSnapshot.change, priceSnapshot.changePct, priceSnapshot.high20, priceSnapshot.latest, priceSnapshot.low20, priceSnapshot.trend20Pct, selected])

  async function handleCopyText() {
    if (!selected) {
      message.warning('请先选择股票')
      return
    }
    try {
      await navigator.clipboard.writeText(shareText)
      message.success('分享文案已复制')
    } catch {
      message.error('复制失败，请手动复制')
    }
  }

  function handleExportShareCard() {
    if (!selected) {
      message.warning('请先选择股票')
      return
    }
    const width = 1080
    const height = 1580
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      message.error('卡片导出失败')
      return
    }

    const gradient = ctx.createLinearGradient(0, 0, width, height)
    gradient.addColorStop(0, '#f5fff8')
    gradient.addColorStop(1, '#e7f6ff')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, width, height)

    ctx.fillStyle = '#0f172a'
    ctx.font = 'bold 52px sans-serif'
    ctx.fillText('Final trade 复盘分享卡', 70, 112)

    ctx.fillStyle = '#1e293b'
    ctx.font = 'bold 70px sans-serif'
    ctx.fillText(selected.name, 70, 220)
    ctx.font = '40px sans-serif'
    ctx.fillText(selected.ts_code, 70, 278)

    ctx.fillStyle = '#334155'
    ctx.font = '34px sans-serif'
    ctx.fillText(`复盘区间: ${dateFrom} ~ ${dateTo}`, 70, 360)
    ctx.fillText(`最新价: ${formatPrice(priceSnapshot.latest)}  (${formatSignedPrice(priceSnapshot.change)} / ${formatSignedPct(priceSnapshot.changePct)})`, 70, 420)
    ctx.fillText(`20日走势: ${trendLabel(priceSnapshot.trend20Pct)} (${formatSignedPct(priceSnapshot.trend20Pct)})`, 70, 476)
    ctx.fillText(`20日区间: ${formatPrice(priceSnapshot.low20)} - ${formatPrice(priceSnapshot.high20)}`, 70, 532)

    ctx.strokeStyle = '#94a3b8'
    ctx.lineWidth = 2
    ctx.strokeRect(70, 575, 940, 200)
    drawSparkline(ctx, priceSnapshot.sparkline, 98, 610, 885, 130)

    let cursorY = 860

    cardInsights.forEach((item) => {
      ctx.fillStyle = '#0f172a'
      ctx.font = 'bold 30px sans-serif'
      ctx.fillText(item.title, 70, cursorY)
      ctx.font = '27px sans-serif'
      drawWrappedText(ctx, item.content, 70, cursorY + 38, 940, 36, item.maxLines)
      cursorY += 38 + item.maxLines * 36 + 22
    })

    if (note.trim()) {
      ctx.fillStyle = '#0f172a'
      ctx.font = 'bold 32px sans-serif'
      ctx.fillText('补充观点', 70, cursorY)
      ctx.font = '28px sans-serif'
      drawWrappedText(ctx, note.trim(), 70, cursorY + 40, 940, 38, 4)
      cursorY += 240
    }

    const footerY = Math.min(height - 36, cursorY + 16)
    ctx.fillStyle = '#475569'
    ctx.font = '26px sans-serif'
    ctx.fillText(`生成时间: ${new Date().toLocaleString()}`, 70, footerY)

    canvas.toBlob((blob) => {
      if (!blob) {
        message.error('卡片导出失败')
        return
      }
      downloadBlob(`share-card-${selected.symbol}-${Date.now()}.png`, blob)
      message.success('分享卡片已导出')
    }, 'image/png')
  }

  function handleOpenChart(row: StockSearchResult) {
    applySelection(row, true)
    const prefixedSymbol = tsCodeToPrefixedSymbol(row.ts_code)
    if (!prefixedSymbol) {
      message.warning('该股票缺少行情代码，无法打开K线页')
      return
    }
    navigate(`/stocks/${prefixedSymbol}/chart`)
  }

  const columns: ColumnsType<StockSearchResult> = [
    { title: '代码', dataIndex: 'ts_code', width: 120 },
    { title: '简称', dataIndex: 'name', width: 140 },
    { title: '行业', dataIndex: 'industry', width: 130 },
    { title: '市场', dataIndex: 'market', width: 100 },
    {
      title: '匹配',
      dataIndex: 'matchType',
      width: 90,
      render: (value: StockSearchResult['matchType']) => (
        <Tag color={value === 'code' ? 'blue' : value === 'name' ? 'green' : 'purple'}>{value}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      render: (_, row) => (
        <Button
          type="link"
          size="small"
          onClick={(event) => {
            event.stopPropagation()
            handleOpenChart(row)
          }}
        >
          查看K线
        </Button>
      ),
    },
  ]

  return (
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small">
        <Space orientation="vertical" size={8} style={{ width: '100%' }}>
          <Typography.Text strong>股票搜索库</Typography.Text>
          <Typography.Text type="secondary">
            支持代码 / 名称 / 拼音检索，数据量 {statsQuery.data?.total ?? '-'} 条
          </Typography.Text>
          <Input
            allowClear
            placeholder="输入代码、名称或拼音（例如：600519 / 贵州茅台 / gzm）"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space wrap>
              <Typography.Text type="secondary">历史选择:</Typography.Text>
              {historyItems.length === 0 ? <Typography.Text type="secondary">暂无</Typography.Text> : null}
              {historyItems.map((item) => (
                <Tag.CheckableTag
                  key={item.ts_code}
                  checked={selected?.ts_code === item.ts_code}
                  onChange={() => {
                    setKeyword(item.ts_code)
                    applySelection(item, true)
                  }}
                >
                  {item.name} {item.ts_code}
                </Tag.CheckableTag>
              ))}
            </Space>
            {historyItems.length > 0 ? (
              <Button
                size="small"
                type="link"
                onClick={() => {
                  setHistoryItems([])
                }}
              >
                清空历史
              </Button>
            ) : null}
          </Space>
          <Table
            rowKey={(row) => row.ts_code}
            loading={loading}
            columns={columns}
            dataSource={rows}
            size="small"
            pagination={{ pageSize: 8, showSizeChanger: false }}
            rowSelection={{
              type: 'radio',
              selectedRowKeys: selected ? [selected.ts_code] : [],
              onChange: (keys) => {
                const key = String(keys[0] || '')
                const row = rows.find((item) => item.ts_code === key) || null
                if (row) applySelection(row)
              },
            }}
            onRow={(record) => ({
              onClick: () => {
                applySelection(record)
              },
            })}
          />
        </Space>
      </Card>

      {candlesQuery.error ? (
        <Alert
          type="warning"
          showIcon
          title="行情加载失败"
          description="当前无法获取该股票价格走势，分享卡片将展示可用复盘内容。"
        />
      ) : null}

      <Card size="small" title="分享卡片预览">
        <Row gutter={[12, 12]}>
          <Col xs={24} lg={14}>
            <Card
              style={{
                borderRadius: 14,
                background: 'linear-gradient(135deg, rgba(241,255,247,0.9), rgba(232,245,255,0.9))',
              }}
            >
              <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                <Typography.Text type="secondary">Final trade</Typography.Text>
                <Typography.Title level={3} style={{ margin: 0 }}>
                  {selected?.name || '请选择一只股票'}
                </Typography.Title>
                <Typography.Text>{selected?.ts_code || '-'}</Typography.Text>
                <Typography.Text>行业: {selected?.industry || '-'}</Typography.Text>
                <Typography.Text>市场: {selected?.market || '-'}</Typography.Text>
                <Typography.Text>复盘区间: {dateFrom} ~ {dateTo}</Typography.Text>
                <Typography.Text>
                  最新价: {formatPrice(priceSnapshot.latest)} ({formatSignedPrice(priceSnapshot.change)} / {formatSignedPct(priceSnapshot.changePct)})
                </Typography.Text>
                <Typography.Text>
                  20日走势: {trendLabel(priceSnapshot.trend20Pct)} ({formatPct(priceSnapshot.trend20Pct ?? 0)})
                </Typography.Text>
                <Typography.Text>
                  20日区间: {formatPrice(priceSnapshot.low20)} - {formatPrice(priceSnapshot.high20)}
                </Typography.Text>
                {klineSummary ? <Typography.Text>K线分析: {klineSummary}</Typography.Text> : null}
                {klineNotes ? <Typography.Text>K线备注: {klineNotes}</Typography.Text> : null}
                {aiSummary ? <Typography.Text>AI分析: {aiSummary}</Typography.Text> : null}
                {aiNotes ? <Typography.Text>AI摘要: {aiNotes}</Typography.Text> : null}
                {stockAnalysisQuery.isFetching ? <Typography.Text type="secondary">正在加载K线分析...</Typography.Text> : null}
                {aiRecordsQuery.isFetching ? <Typography.Text type="secondary">正在加载AI分析...</Typography.Text> : null}
                {candlesQuery.isLoading ? <Typography.Text type="secondary">正在加载走势数据...</Typography.Text> : null}
              </Space>
            </Card>
          </Col>
          <Col xs={24} lg={10}>
            <Space orientation="vertical" style={{ width: '100%' }}>
              <Input.TextArea
                rows={6}
                placeholder="可选补充观点，导出卡片时会带上"
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <Space>
                <Button type="primary" onClick={handleExportShareCard} disabled={!selected}>
                  导出分享卡片 PNG
                </Button>
                <Button onClick={handleCopyText} disabled={!selected}>
                  复制分享文案
                </Button>
              </Space>
              <Input.TextArea value={shareText} rows={10} readOnly />
            </Space>
          </Col>
        </Row>
      </Card>
    </Space>
  )
}
