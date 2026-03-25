import { useMemo, useRef, useEffect, useState } from 'react'
import type { Stock, FilterState } from '../types'

interface RidgelinePlotProps {
  allStocks: Stock[]
  filters: FilterState
}

const MARGIN = { top: 16, right: 20, bottom: 36, left: 80 }

const STAGES = [
  { key: 'raw', label: 'Raw', color: '#8b5cf6' },
  { key: 'series', label: 'Series', color: '#a855f7' },
  { key: 'price', label: 'Price', color: '#6366f1' },
  { key: 'volume', label: 'Volume', color: '#3b82f6' },
  { key: 'turnover', label: 'Turnover', color: '#06b6d4' },
  { key: 'final', label: 'Final', color: '#10b981' },
] as const

function formatAxis(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(0)}B`
  if (value >= 10_000_000) return `${(value / 10_000_000).toFixed(0)}Cr`
  if (value >= 100_000) return `${(value / 100_000).toFixed(0)}L`
  if (value >= 1000) return `${(value / 1000).toFixed(0)}K`
  return value.toString()
}

function kernelDensity(
  values: number[],
  domain: [number, number],
  bandwidth: number,
  nPoints: number = 80
): [number, number][] {
  if (values.length === 0) return []
  const [lo, hi] = domain
  const step = (hi - lo) / (nPoints - 1)
  const points: [number, number][] = []
  for (let i = 0; i < nPoints; i++) {
    const x = lo + i * step
    let sum = 0
    for (const v of values) {
      const u = (x - v) / bandwidth
      sum += Math.exp(-0.5 * u * u) / (bandwidth * Math.sqrt(2 * Math.PI))
    }
    points.push([x, sum / values.length])
  }
  return points
}

function logScale(
  domain: [number, number],
  range: [number, number]
): (v: number) => number {
  const [d0, d1] = domain.map(Math.log10)
  const [r0, r1] = range
  return (v: number) => {
    const t = (Math.log10(Math.max(v, 1)) - d0) / (d1 - d0)
    return r0 + t * (r1 - r0)
  }
}

function logTicks(domain: [number, number]): number[] {
  const minE = Math.ceil(Math.log10(Math.max(domain[0], 1)))
  const maxE = Math.floor(Math.log10(domain[1]))
  const ticks: number[] = []
  for (let e = minE; e <= maxE; e++) ticks.push(Math.pow(10, e))
  return ticks
}

export function RidgelinePlot({ allStocks, filters }: RidgelinePlotProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ width: 500, height: 340 })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) setDims({ width, height })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  const w = dims.width - MARGIN.left - MARGIN.right
  const h = dims.height - MARGIN.top - MARGIN.bottom

  const { curves, xDomain } = useMemo(() => {
    const positiveStocks = allStocks.filter((s) => s.avgTurnover20d > 0)
    if (positiveStocks.length === 0)
      return { curves: [], xDomain: [1, 1] as [number, number] }

    const allLogTO = positiveStocks.map((s) => Math.log10(s.avgTurnover20d))
    const xDom: [number, number] = [
      Math.min(...allLogTO) - 0.2,
      Math.max(...allLogTO) + 0.2,
    ]
    const bandwidth = (xDom[1] - xDom[0]) / 15

    // Build filter stages
    let remaining = positiveStocks
    const stageSets: Stock[][] = [remaining] // raw

    // Series
    if (filters.series.length > 0) {
      remaining = remaining.filter((s) => filters.series.includes(s.series))
    }
    stageSets.push(remaining)

    // Price
    remaining = remaining.filter(
      (s) => s.lastPrice >= filters.priceMin && s.lastPrice <= filters.priceMax
    )
    stageSets.push(remaining)

    // Volume
    remaining = remaining.filter((s) => s.avgVolume20d >= filters.volumeMin)
    stageSets.push(remaining)

    // Turnover
    remaining = remaining.filter(
      (s) => s.avgTurnover20d >= filters.turnoverMin
    )
    stageSets.push(remaining)

    // Traded days
    remaining = remaining.filter(
      (s) => s.tradedDays >= filters.minTradedDays
    )
    stageSets.push(remaining)

    const curveData = STAGES.map((stage, i) => {
      const vals = stageSets[i].map((s) => Math.log10(s.avgTurnover20d))
      const density = kernelDensity(vals, xDom, bandwidth)
      return {
        ...stage,
        density,
        count: stageSets[i].length,
      }
    })

    return {
      curves: curveData,
      xDomain: [Math.pow(10, xDom[0]), Math.pow(10, xDom[1])] as [
        number,
        number,
      ],
    }
  }, [allStocks, filters])

  const xTicks = useMemo(() => logTicks(xDomain), [xDomain])
  const xScale = useMemo(() => logScale(xDomain, [0, w]), [xDomain, w])

  const rowHeight = curves.length > 0 ? h / curves.length : 40
  const maxDensity = useMemo(() => {
    let m = 0
    for (const c of curves)
      for (const [, y] of c.density) if (y > m) m = y
    return m || 1
  }, [curves])

  const curveHeight = rowHeight * 1.8

  return (
    <div ref={containerRef} className='h-full w-full'>
      <svg width={dims.width} height={dims.height}>
        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* X axis */}
          <line x1={0} y1={h} x2={w} y2={h} stroke='#374151' />
          {xTicks.map((t) => {
            const x = xScale(t)
            if (x < 0 || x > w) return null
            return (
              <g key={`x-${t}`}>
                <line x1={x} y1={h} x2={x} y2={h + 4} stroke='#374151' />
                <text
                  x={x}
                  y={h + 16}
                  textAnchor='middle'
                  className='fill-gray-400 text-[10px]'
                >
                  {formatAxis(t)}
                </text>
              </g>
            )
          })}
          <text
            x={w / 2}
            y={h + 32}
            textAnchor='middle'
            className='fill-gray-400 text-[10px]'
          >
            Avg Turnover (log)
          </text>

          {/* Grid lines */}
          {xTicks.map((t) => {
            const x = xScale(t)
            if (x < 0 || x > w) return null
            return (
              <line
                key={`g-${t}`}
                x1={x}
                y1={0}
                x2={x}
                y2={h}
                stroke='#374151'
                strokeOpacity={0.3}
                strokeDasharray='2,4'
              />
            )
          })}

          {/* Ridgeline curves — render bottom-up so earlier stages are behind */}
          {[...curves].reverse().map((curve, ri) => {
            const i = curves.length - 1 - ri
            const baseY = MARGIN.top + (i + 1) * rowHeight
            if (curve.density.length === 0) return null

            // Convert log10 x values back to actual turnover for xScale
            const pathPoints = curve.density.map(([logX, y]) => {
              const actualX = Math.pow(10, logX)
              return [xScale(actualX), baseY - (y / maxDensity) * curveHeight] as [
                number,
                number,
              ]
            })

            const d =
              `M ${pathPoints[0][0]},${baseY} ` +
              pathPoints.map(([x, y]) => `L ${x},${y}`).join(' ') +
              ` L ${pathPoints[pathPoints.length - 1][0]},${baseY} Z`

            return (
              <g key={curve.key}>
                <path
                  d={d}
                  fill={curve.color}
                  fillOpacity={0.25}
                  stroke={curve.color}
                  strokeWidth={1.5}
                  strokeOpacity={0.8}
                />
                <text
                  x={-8}
                  y={baseY - rowHeight * 0.3}
                  textAnchor='end'
                  dominantBaseline='central'
                  className='fill-gray-300 text-[10px]'
                >
                  {curve.label} ({curve.count})
                </text>
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}
