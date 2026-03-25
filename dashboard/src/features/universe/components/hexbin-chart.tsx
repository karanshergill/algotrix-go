import { useMemo, useRef, useEffect, useState } from 'react'
import { hexbin as d3Hexbin } from 'd3-hexbin'
import type { FilteredStock, DepthTier } from '../types'

interface HexbinChartProps {
  stocks: FilteredStock[]
  onStockClick?: (isin: string) => void
}

const TIER_BORDER: Record<string, string> = {
  D50: '#ef4444',
  D30: '#f59e0b',
}

const MARGIN = { top: 12, right: 16, bottom: 36, left: 56 }

function formatAxis(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(0)}B`
  if (value >= 10_000_000) return `${(value / 10_000_000).toFixed(0)}Cr`
  if (value >= 100_000) return `${(value / 100_000).toFixed(0)}L`
  if (value >= 1000) return `${(value / 1000).toFixed(0)}K`
  return value.toString()
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

export function HexbinChart({ stocks }: HexbinChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dims, setDims] = useState({ width: 500, height: 340 })
  const [hoveredBin, setHoveredBin] = useState<number | null>(null)
  const [tooltip, setTooltip] = useState<{
    x: number
    y: number
    count: number
    hasD50: boolean
    hasD30: boolean
  } | null>(null)

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

  const { bins, xScale, yScale, maxCount, xDomain, yDomain } = useMemo(() => {
    const eligible = stocks.filter(
      (s) => s.pass && s.avgTurnover20d > 0 && s.avgVolume20d > 0
    )
    if (eligible.length === 0)
      return {
        bins: [],
        xScale: () => 0,
        yScale: () => 0,
        maxCount: 0,
        xDomain: [1, 1] as [number, number],
        yDomain: [1, 1] as [number, number],
      }

    const turnovers = eligible.map((s) => s.avgTurnover20d)
    const volumes = eligible.map((s) => s.avgVolume20d)
    const xDom: [number, number] = [
      Math.min(...turnovers) * 0.8,
      Math.max(...turnovers) * 1.2,
    ]
    const yDom: [number, number] = [
      Math.min(...volumes) * 0.8,
      Math.max(...volumes) * 1.2,
    ]

    const xs = logScale(xDom, [0, w])
    const ys = logScale(yDom, [h, 0])

    const hexRadius = Math.max(12, Math.min(20, w / 30))
    const hexbin = d3Hexbin<{
      x: number
      y: number
      tier: DepthTier
    }>()
      .x((d) => d.x)
      .y((d) => d.y)
      .radius(hexRadius)
      .extent([
        [0, 0],
        [w, h],
      ])

    const points = eligible.map((s) => ({
      x: xs(s.avgTurnover20d),
      y: ys(s.avgVolume20d),
      tier: s.tier,
    }))

    const rawBins = hexbin(points)
    const mc = Math.max(...rawBins.map((b) => b.length), 1)

    return {
      bins: rawBins.map((b) => ({
        x: b.x,
        y: b.y,
        count: b.length,
        hasD50: b.some((p) => p.tier === 'D50'),
        hasD30: b.some((p) => p.tier === 'D30'),
        path: hexbin.hexagon(),
      })),
      xScale: xs,
      yScale: ys,
      maxCount: mc,
      xDomain: xDom,
      yDomain: yDom,
    }
  }, [stocks, w, h])

  const xTicks = useMemo(() => logTicks(xDomain), [xDomain])
  const yTicks = useMemo(() => logTicks(yDomain), [yDomain])

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

          {/* Y axis */}
          <line x1={0} y1={0} x2={0} y2={h} stroke='#374151' />
          {yTicks.map((t) => {
            const y = yScale(t)
            if (y < 0 || y > h) return null
            return (
              <g key={`y-${t}`}>
                <line x1={-4} y1={y} x2={0} y2={y} stroke='#374151' />
                <text
                  x={-8}
                  y={y}
                  textAnchor='end'
                  dominantBaseline='central'
                  className='fill-gray-400 text-[10px]'
                >
                  {formatAxis(t)}
                </text>
              </g>
            )
          })}
          <text
            x={-MARGIN.left + 12}
            y={h / 2}
            textAnchor='middle'
            dominantBaseline='central'
            transform={`rotate(-90, ${-MARGIN.left + 12}, ${h / 2})`}
            className='fill-gray-400 text-[10px]'
          >
            Avg Volume (log)
          </text>

          {/* Hexbins */}
          {bins.map((bin, i) => {
            const intensity = Math.max(0.15, bin.count / maxCount)
            const isHovered = hoveredBin === i
            const borderTier = bin.hasD50
              ? TIER_BORDER.D50
              : bin.hasD30
                ? TIER_BORDER.D30
                : null
            return (
              <g
                key={i}
                transform={`translate(${bin.x},${bin.y})`}
                onMouseEnter={() => {
                  setHoveredBin(i)
                  setTooltip({
                    x: bin.x + MARGIN.left,
                    y: bin.y + MARGIN.top,
                    count: bin.count,
                    hasD50: bin.hasD50,
                    hasD30: bin.hasD30,
                  })
                }}
                onMouseLeave={() => {
                  setHoveredBin(null)
                  setTooltip(null)
                }}
              >
                <path
                  d={bin.path}
                  fill='#8b5cf6'
                  fillOpacity={isHovered ? Math.min(intensity + 0.2, 1) : intensity}
                  stroke={borderTier ?? '#1f293780'}
                  strokeWidth={borderTier ? 2 : 0.5}
                  className='transition-opacity duration-100'
                />
                {borderTier && (
                  <path
                    d={bin.path}
                    fill='none'
                    stroke={borderTier}
                    strokeWidth={2}
                    strokeOpacity={0.8}
                    filter='url(#glow)'
                  />
                )}
              </g>
            )
          })}
        </g>

        {/* Glow filter */}
        <defs>
          <filter id='glow'>
            <feGaussianBlur stdDeviation='2' result='blur' />
            <feMerge>
              <feMergeNode in='blur' />
              <feMergeNode in='SourceGraphic' />
            </feMerge>
          </filter>
        </defs>
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div
          className='pointer-events-none absolute rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-lg'
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 10,
            transform: 'translateY(-100%)',
          }}
        >
          <p className='font-semibold text-foreground'>
            {tooltip.count} stock{tooltip.count !== 1 ? 's' : ''}
          </p>
          {tooltip.hasD50 && (
            <p style={{ color: TIER_BORDER.D50 }}>Contains D50</p>
          )}
          {tooltip.hasD30 && (
            <p style={{ color: TIER_BORDER.D30 }}>Contains D30</p>
          )}
        </div>
      )}
    </div>
  )
}
