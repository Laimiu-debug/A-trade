import { useEffect, useMemo, useRef } from 'react'
import ReactECharts from 'echarts-for-react'
import type { IntradayPoint } from '@/types/contracts'

interface IntradayChartProps {
  points: IntradayPoint[]
  referencePrice?: number | null
}

function toSignedPercentFromBase(basePrice: number, price: number, digits = 2) {
  if (!Number.isFinite(basePrice) || basePrice <= 0 || !Number.isFinite(price)) return '--'
  const pct = ((price / basePrice) - 1) * 100
  if (pct > 0) return `+${pct.toFixed(digits)}%`
  return `${pct.toFixed(digits)}%`
}

function toSignedPercent(value: number, digits = 2) {
  if (!Number.isFinite(value)) return '--'
  if (value > 0) return `+${value.toFixed(digits)}%`
  return `${value.toFixed(digits)}%`
}

function toSafePrice(value: number) {
  if (!Number.isFinite(value)) return '--'
  return Number(value).toFixed(2)
}

function toSafeVolume(value: number) {
  if (!Number.isFinite(value)) return '--'
  return Number(value).toLocaleString('zh-CN')
}

export function IntradayChart({ points, referencePrice }: IntradayChartProps) {
  const chartRef = useRef<ReactECharts>(null)
  const xData = useMemo(() => points.map((item) => item.time), [points])
  const priceData = useMemo(() => points.map((item) => item.price), [points])
  const avgPriceData = useMemo(() => points.map((item) => item.avg_price), [points])
  const volumeData = useMemo(() => points.map((item) => item.volume), [points])
  const basePrice = useMemo(() => {
    const preferred = Number(referencePrice ?? NaN)
    if (Number.isFinite(preferred) && preferred > 0) return preferred
    const first = points.find((item) => Number.isFinite(item.price) && Number(item.price) > 0)
    return first ? Number(first.price) : 0
  }, [points, referencePrice])
  const priceExtent = useMemo(() => {
    const values = [...priceData, ...avgPriceData].filter((item) => Number.isFinite(item))
    if (values.length === 0) {
      return { min: 0, max: 1 }
    }
    if (!Number.isFinite(basePrice) || basePrice <= 0) {
      const minValue = Math.min(...values)
      const maxValue = Math.max(...values)
      const range = maxValue - minValue
      const padding = range > 0 ? range * 0.08 : Math.max(Math.abs(maxValue) * 0.02, 0.01)
      return {
        min: Number((minValue - padding).toFixed(2)),
        max: Number((maxValue + padding).toFixed(2)),
      }
    }

    const maxOffset = values.reduce((currentMax, value) => Math.max(currentMax, Math.abs(value - basePrice)), 0)
    const padding = Math.max(maxOffset * 1.08, basePrice * 0.003, 0.01)
    return {
      min: Number((basePrice - padding).toFixed(2)),
      max: Number((basePrice + padding).toFixed(2)),
    }
  }, [avgPriceData, basePrice, priceData])
  const percentExtent = useMemo(() => {
    if (!Number.isFinite(basePrice) || basePrice <= 0) {
      return { min: -1, max: 1 }
    }
    return {
      min: Number((((priceExtent.min / basePrice) - 1) * 100).toFixed(2)),
      max: Number((((priceExtent.max / basePrice) - 1) * 100).toFixed(2)),
    }
  }, [basePrice, priceExtent.max, priceExtent.min])

  const option = useMemo(
    () => ({
      animation: true,
      legend: {
        top: 2,
        textStyle: { color: '#2f5452' },
        data: ['分时价', '均价', '成交量'],
      },
      tooltip: {
        trigger: 'axis',
        formatter: (
          rawParams:
            | Array<{ axisValue?: string; dataIndex?: number }>
            | { axisValue?: string; dataIndex?: number },
        ) => {
          const params = Array.isArray(rawParams) ? rawParams : [rawParams]
          const axisValue = String(params[0]?.axisValue ?? '')
          const dataIndex = Number(params[0]?.dataIndex ?? -1)
          const point = dataIndex >= 0 && dataIndex < points.length ? points[dataIndex] : null
          if (!point) return `<div>${axisValue}</div>`
          const price = Number(point.price)
          const avgPrice = Number(point.avg_price)
          const volume = Number(point.volume)
          const pricePct = toSignedPercentFromBase(basePrice, price, 2)
          const avgPct = toSignedPercentFromBase(basePrice, avgPrice, 2)
          return [
            '<div style="min-width: 240px">',
            `<div style="font-weight: 600; margin-bottom: 6px">${axisValue}</div>`,
            `<div>分时价：${toSafePrice(price)} (${pricePct})</div>`,
            `<div>均价：${toSafePrice(avgPrice)} (${avgPct})</div>`,
            `<div>成交量：${toSafeVolume(volume)}</div>`,
            '</div>',
          ].join('')
        },
      },
      axisPointer: {
        link: [{ xAxisIndex: [0, 1] }],
      },
      grid: [
        { left: '6%', right: '5%', top: '12%', height: '56%' },
        { left: '6%', right: '5%', top: '75%', height: '16%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: xData,
          boundaryGap: false,
        },
        {
          type: 'category',
          gridIndex: 1,
          data: xData,
          boundaryGap: false,
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          min: priceExtent.min,
          max: priceExtent.max,
          splitNumber: 6,
          axisLabel: {
            formatter: (value: number) => toSafePrice(Number(value)),
          },
          splitLine: { lineStyle: { color: 'rgba(31,49,48,0.14)' } },
        },
        {
          min: percentExtent.min,
          max: percentExtent.max,
          splitNumber: 6,
          position: 'right',
          axisLabel: {
            formatter: (value: number) => toSignedPercent(Number(value), 2),
          },
          splitLine: { show: false },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        { show: true, xAxisIndex: [0, 1], type: 'slider', top: '93%', start: 0, end: 100 },
      ],
      series: [
        {
          name: '分时价',
          type: 'line',
          data: priceData,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.7, color: '#0f8b6f' },
          markLine: basePrice > 0
            ? {
                symbol: ['none', 'none'],
                silent: true,
                label: {
                  show: true,
                  formatter: `昨收 ${toSafePrice(basePrice)} / 0.00%`,
                  color: '#64748b',
                  backgroundColor: 'rgba(255,255,255,0.85)',
                  padding: [2, 6],
                },
                lineStyle: {
                  color: '#94a3b8',
                  width: 1,
                  type: 'dashed',
                },
                data: [{ yAxis: basePrice }],
              }
            : undefined,
        },
        {
          name: '均价',
          type: 'line',
          data: avgPriceData,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 1.4, color: '#e88e1a' },
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 2,
          data: volumeData,
          itemStyle: { color: '#8ca9a7' },
        },
      ],
    }),
    [avgPriceData, basePrice, percentExtent.max, percentExtent.min, points, priceData, priceExtent.max, priceExtent.min, volumeData, xData],
  )

  useEffect(() => {
    if (points.length === 0) return
    const frameId = window.requestAnimationFrame(() => {
      chartRef.current?.getEchartsInstance().resize()
    })
    return () => {
      window.cancelAnimationFrame(frameId)
    }
  }, [points.length])

  return (
    <ReactECharts
      ref={chartRef}
      option={option}
      style={{ width: '100%', height: 420 }}
    />
  )
}
