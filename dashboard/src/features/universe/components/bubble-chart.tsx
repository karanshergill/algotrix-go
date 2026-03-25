import { useMemo, useState } from 'react'
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'
import type { FilteredStock, DepthTier } from '../types'

interface BubbleChartProps {
  stocks: FilteredStock[]
  onStockClick?: (isin: string) => void
}

const TIER_COLORS: Record<DepthTier, string> = {
  D50: '#ef4444',
  D30: '#f59e0b',
  D5: '#3b82f6',
  none: '#6b728080',
}

function formatAxis(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(0)}B`
  if (value >= 10_000_000) return `${(value / 10_000_000).toFixed(0)}Cr`
  if (value >= 100_000) return `${(value / 100_000).toFixed(0)}L`
  if (value >= 1000) return `${(value / 1000).toFixed(0)}K`
  return value.toString()
}

export function BubbleChart({ stocks, onStockClick }: BubbleChartProps) {
  const [activeTier, setActiveTier] = useState<DepthTier | null>(null)

  const data = useMemo(() => {
    return stocks
      .filter((s) => s.avgTurnover20d > 0 && s.avgVolume20d > 0)
      .map((s) => ({
        x: s.avgTurnover20d,
        y: s.avgVolume20d,
        z: Math.max(s.lastPrice / 200, 3),
        symbol: s.symbol,
        isin: s.isin,
        tier: s.tier,
        lastPrice: s.lastPrice,
        sector: s.sector,
      }))
  }, [stocks])

  const tierGroups = useMemo(() => {
    const groups: Record<string, typeof data> = {}
    for (const d of data) {
      const key = d.tier
      if (!groups[key]) groups[key] = []
      groups[key].push(d)
    }
    return groups
  }, [data])

  const tiers: DepthTier[] = ['D50', 'D30', 'D5', 'none']

  return (
    <div className='flex h-full flex-col'>
      <div className='mb-2 flex items-center gap-3 px-2'>
        {(['D50', 'D30', 'D5'] as DepthTier[]).map((tier) => (
          <button
            key={tier}
            className='flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground'
            onMouseEnter={() => setActiveTier(tier)}
            onMouseLeave={() => setActiveTier(null)}
          >
            <span
              className='inline-block size-2.5 rounded-full'
              style={{ backgroundColor: TIER_COLORS[tier] }}
            />
            {tier}
          </button>
        ))}
      </div>
      <div className='flex-1'>
        <ResponsiveContainer width='100%' height='100%'>
          <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
            <XAxis
              dataKey='x'
              type='number'
              scale='log'
              domain={['auto', 'auto']}
              tickFormatter={formatAxis}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              axisLine={{ stroke: '#374151' }}
              tickLine={{ stroke: '#374151' }}
              label={{
                value: 'Avg Turnover',
                position: 'insideBottom',
                offset: -16,
                style: { fontSize: 10, fill: '#9ca3af' },
              }}
            />
            <YAxis
              dataKey='y'
              type='number'
              scale='log'
              domain={['auto', 'auto']}
              tickFormatter={formatAxis}
              tick={{ fontSize: 10, fill: '#9ca3af' }}
              axisLine={{ stroke: '#374151' }}
              tickLine={{ stroke: '#374151' }}
              label={{
                value: 'Avg Volume',
                angle: -90,
                position: 'insideLeft',
                offset: 4,
                style: { fontSize: 10, fill: '#9ca3af' },
              }}
            />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.[0]) return null
                const d = payload[0].payload
                return (
                  <div className='rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-lg'>
                    <p className='font-semibold text-foreground'>{d.symbol}</p>
                    <p className='text-muted-foreground'>
                      Price: ₹{d.lastPrice.toLocaleString()}
                    </p>
                    <p className='text-muted-foreground'>
                      Vol: {formatAxis(d.y)} | TO: {formatAxis(d.x)}
                    </p>
                    <p className='text-muted-foreground'>
                      Sector: {d.sector ?? '—'}
                    </p>
                    <p style={{ color: TIER_COLORS[d.tier as DepthTier] }}>
                      Tier: {d.tier === 'none' ? 'Failed' : d.tier}
                    </p>
                  </div>
                )
              }}
            />
            {tiers.map((tier) => {
              const group = tierGroups[tier]
              if (!group?.length) return null
              return (
                <Scatter
                  key={tier}
                  data={group}
                  onClick={(entry) => onStockClick?.(entry.isin)}
                  cursor='pointer'
                >
                  {group.map((entry, j) => (
                    <Cell
                      key={j}
                      fill={TIER_COLORS[tier]}
                      fillOpacity={
                        activeTier === null
                          ? tier === 'none'
                            ? 0.2
                            : 0.7
                          : activeTier === tier
                            ? 0.9
                            : 0.08
                      }
                      r={entry.z}
                    />
                  ))}
                </Scatter>
              )
            })}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
