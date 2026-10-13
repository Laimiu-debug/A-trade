import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ReloadOutlined, SettingOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Checkbox, Input, Popover, Radio, Row, Col, Space, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { getStockCandles } from '@/shared/api/endpoints'

type IndexDefinition = {
  symbol: string
  code: string
  name: string
}

type IndexSnapshot = IndexDefinition & {
  date: string
  price: number
  changePct: number
  high: number
  low: number
}

const INDEX_STORAGE_KEY = 'final-trade-review-index-panel-selected-v1'
const MOOD_STORAGE_KEY = 'final-trade-review-index-panel-mood-v1'
const MOOD_NOTE_STORAGE_KEY = 'final-trade-review-index-panel-mood-note-v1'

type MarketMood = 'bullish' | 'neutral' | 'bearish'

const INDEX_UNIVERSE: IndexDefinition[] = [
  { symbol: 'sh000001', code: '000001.SH', name: '上证指数' },
  { symbol: 'sh000300', code: '000300.SH', name: '沪深300' },
  { symbol: 'sz399001', code: '399001.SZ', name: '深证成指' },
  { symbol: 'sz399006', code: '399006.SZ', name: '创业板指' },
  { symbol: 'sh000016', code: '000016.SH', name: '上证50' },
  { symbol: 'sh000905', code: '000905.SH', name: '中证500' },
  { symbol: 'sh000852', code: '000852.SH', name: '中证1000' },
  { symbol: 'sh000688', code: '000688.SH', name: '科创50' },
]

const DEFAULT_SELECTED_SYMBOLS = ['sh000001', 'sh000300', 'sz399001', 'sz399006']

function loadSelectedSymbols() {
  if (typeof window === 'undefined') return DEFAULT_SELECTED_SYMBOLS
  try {
    const raw = window.localStorage.getItem(INDEX_STORAGE_KEY)
    if (!raw) return DEFAULT_SELECTED_SYMBOLS
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return DEFAULT_SELECTED_SYMBOLS
    const valid = parsed
      .map((item) => String(item))
      .filter((item) => INDEX_UNIVERSE.some((entry) => entry.symbol === item))
    return valid.length > 0 ? valid : DEFAULT_SELECTED_SYMBOLS
  } catch {
    return DEFAULT_SELECTED_SYMBOLS
  }
}

function saveSelectedSymbols(symbols: string[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(INDEX_STORAGE_KEY, JSON.stringify(symbols))
  } catch {
    // ignore localStorage errors
  }
}

function loadMood(): MarketMood {
  if (typeof window === 'undefined') return 'neutral'
  const raw = window.localStorage.getItem(MOOD_STORAGE_KEY)
  if (raw === 'bullish' || raw === 'neutral' || raw === 'bearish') return raw
  return 'neutral'
}

function saveMood(mood: MarketMood) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(MOOD_STORAGE_KEY, mood)
  } catch {
    // ignore localStorage errors
  }
}

function loadMoodNote() {
  if (typeof window === 'undefined') return ''
  return window.localStorage.getItem(MOOD_NOTE_STORAGE_KEY) || ''
}

function saveMoodNote(note: string) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(MOOD_NOTE_STORAGE_KEY, note)
  } catch {
    // ignore localStorage errors
  }
}

function formatSignedPct(value: number) {
  if (!Number.isFinite(value)) return '--'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

function toSnapshot(definition: IndexDefinition, candles: Array<{ time: string; close: number; high: number; low: number }>) {
  if (candles.length === 0) return null
  const latest = candles[candles.length - 1]
  const previous = candles.length > 1 ? candles[candles.length - 2] : null
  const changePct = previous && previous.close > 0 ? (latest.close - previous.close) / previous.close : 0
  return {
    ...definition,
    date: latest.time,
    price: latest.close,
    changePct,
    high: latest.high,
    low: latest.low,
  } satisfies IndexSnapshot
}

export function ReviewMarketIndicesPanel() {
  const navigate = useNavigate()
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(() => loadSelectedSymbols())
  const [configOpen, setConfigOpen] = useState(false)
  const [searchKeyword, setSearchKeyword] = useState('')
  const [marketMood, setMarketMood] = useState<MarketMood>(() => loadMood())
  const [moodNote, setMoodNote] = useState(() => loadMoodNote())

  const selectedSet = useMemo(() => new Set(selectedSymbols), [selectedSymbols])
  const selectedDefinitions = useMemo(
    () => INDEX_UNIVERSE.filter((item) => selectedSet.has(item.symbol)),
    [selectedSet],
  )

  const query = useQuery({
    queryKey: ['review-market-indices', selectedSymbols.join(',')],
    queryFn: async () => {
      const items = await Promise.all(
        selectedDefinitions.map(async (definition) => {
          const payload = await getStockCandles(definition.symbol)
          return toSnapshot(definition, payload.candles)
        }),
      )
      return items.filter((item): item is IndexSnapshot => Boolean(item))
    },
    enabled: selectedDefinitions.length > 0,
    staleTime: 60_000,
    refetchInterval: 5 * 60 * 1000,
    refetchIntervalInBackground: true,
  })

  const marketSummary = useMemo(() => {
    const items = query.data ?? []
    if (items.length === 0) return null
    const upCount = items.filter((item) => item.changePct > 0).length
    const downCount = items.filter((item) => item.changePct < 0).length
    const avgChange = items.reduce((sum, item) => sum + item.changePct, 0) / items.length
    return { upCount, downCount, avgChange }
  }, [query.data])

  const filteredUniverse = useMemo(() => {
    const keyword = searchKeyword.trim().toLowerCase()
    if (!keyword) return INDEX_UNIVERSE
    return INDEX_UNIVERSE.filter(
      (item) => item.name.toLowerCase().includes(keyword) || item.code.toLowerCase().includes(keyword),
    )
  }, [searchKeyword])

  const configOptions = useMemo(
    () =>
      filteredUniverse.map((item) => ({
        label: `${item.name} (${item.code})`,
        value: item.symbol,
      })),
    [filteredUniverse],
  )

  function applySelectedSymbols(nextSymbols: string[]) {
    const normalized = INDEX_UNIVERSE
      .map((item) => item.symbol)
      .filter((symbol) => nextSymbols.includes(symbol))
    const finalSelection = normalized.length > 0 ? normalized : DEFAULT_SELECTED_SYMBOLS
    setSelectedSymbols(finalSelection)
    saveSelectedSymbols(finalSelection)
  }

  function handleMoodChange(nextMood: MarketMood) {
    setMarketMood(nextMood)
    saveMood(nextMood)
  }

  function handleMoodNoteChange(nextNote: string) {
    setMoodNote(nextNote)
    saveMoodNote(nextNote)
  }

  function openIndexTrend(item: IndexSnapshot) {
    const params = new URLSearchParams({ signal_stock_name: item.name })
    navigate(`/stocks/${item.symbol}/chart?${params.toString()}`)
  }

  return (
    <Card
      className="glass-card"
      variant="borderless"
      title={
        <Space size={8}>
          <span aria-hidden>📊</span>
          <Typography.Text strong>大盘指数与关键数据</Typography.Text>
        </Space>
      }
      extra={
        <Space size={8}>
          <Popover
            trigger="click"
            open={configOpen}
            onOpenChange={setConfigOpen}
            placement="bottomRight"
            content={
              <Space orientation="vertical" size={10} style={{ width: 360, maxWidth: '80vw' }}>
                <Input
                  allowClear
                  value={searchKeyword}
                  onChange={(event) => setSearchKeyword(event.target.value)}
                  placeholder="搜索指数名称或代码"
                />
                <Space>
                  <Button size="small" onClick={() => applySelectedSymbols(DEFAULT_SELECTED_SYMBOLS)}>
                    恢复默认
                  </Button>
                  <Button size="small" onClick={() => applySelectedSymbols(INDEX_UNIVERSE.map((item) => item.symbol))}>
                    全选
                  </Button>
                </Space>
                <Checkbox.Group
                  style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 6 }}
                  value={selectedSymbols}
                  options={configOptions}
                  onChange={(values) => applySelectedSymbols(values.map((item) => String(item)))}
                />
              </Space>
            }
          >
            <Button size="small" icon={<SettingOutlined />}>
              配置
            </Button>
          </Popover>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => void query.refetch()} loading={query.isFetching}>
            刷新
          </Button>
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        {marketSummary ? (
          <div
            style={{
              borderRadius: 10,
              background: 'rgba(31,49,48,0.06)',
              padding: '10px 12px',
            }}
          >
            <Space size={18} wrap>
              <Typography.Text>
                涨/跌: <span style={{ color: '#c4473d', fontWeight: 600 }}>{marketSummary.upCount}</span>
                {' / '}
                <span style={{ color: '#19744f', fontWeight: 600 }}>{marketSummary.downCount}</span>
              </Typography.Text>
              <Typography.Text>
                平均涨跌:{' '}
                <span style={{ color: marketSummary.avgChange >= 0 ? '#c4473d' : '#19744f', fontWeight: 600 }}>
                  {formatSignedPct(marketSummary.avgChange)}
                </span>
              </Typography.Text>
            </Space>
          </div>
        ) : null}

        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Typography.Text type="secondary">
            主要指数 ({query.data?.length ?? 0}/{INDEX_UNIVERSE.length})
          </Typography.Text>
          <Typography.Text type="secondary">
            {query.data && query.data.length > 0 ? `数据日期: ${query.data[0].date}` : '数据日期: -'}
          </Typography.Text>
        </Space>

        {query.error ? (
          <Alert showIcon type="warning" title="指数数据加载失败" description={query.error instanceof Error ? query.error.message : '请稍后重试'} />
        ) : null}

        {query.data && query.data.length > 0 ? (
          <Row gutter={[12, 12]}>
            {query.data.map((item) => {
              const isUp = item.changePct >= 0
              return (
                <Col key={item.symbol} xs={24} sm={12} lg={6}>
                  <Card
                    size="small"
                    hoverable
                    style={{ borderRadius: 12 }}
                    onClick={() => openIndexTrend(item)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        openIndexTrend(item)
                      }
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <Space orientation="vertical" size={2} style={{ width: '100%' }}>
                      <Typography.Text type="secondary">{item.name}</Typography.Text>
                      <Typography.Text strong style={{ fontSize: 34, lineHeight: '38px' }}>
                        {item.price.toFixed(2)}
                      </Typography.Text>
                      <Typography.Text style={{ color: isUp ? '#c4473d' : '#19744f', fontWeight: 600 }}>
                        {formatSignedPct(item.changePct)}
                      </Typography.Text>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Typography.Text type="secondary">最高: {item.high.toFixed(2)}</Typography.Text>
                        <Typography.Text type="secondary">最低: {item.low.toFixed(2)}</Typography.Text>
                      </Space>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        点击查看走势
                      </Typography.Text>
                    </Space>
                  </Card>
                </Col>
              )
            })}
          </Row>
        ) : null}

        <div style={{ borderTop: '1px solid rgba(31,49,48,0.08)', paddingTop: 12 }}>
          <Space orientation="vertical" size={10} style={{ width: '100%' }}>
            <Typography.Text type="secondary">市场情绪判断</Typography.Text>
            <Radio.Group
              optionType="button"
              value={marketMood}
              options={[
                { label: '看多 📈', value: 'bullish' },
                { label: '中性 ➡️', value: 'neutral' },
                { label: '看空 📉', value: 'bearish' },
              ]}
              onChange={(event) => handleMoodChange(event.target.value as MarketMood)}
            />
            <Input.TextArea
              rows={2}
              value={moodNote}
              onChange={(event) => handleMoodNoteChange(event.target.value)}
              placeholder="记录今日市场观察和情绪判断..."
              maxLength={300}
              showCount
            />
          </Space>
        </div>

        {query.isLoading && !query.data ? <Typography.Text type="secondary">指数数据加载中...</Typography.Text> : null}
      </Space>
    </Card>
  )
}
