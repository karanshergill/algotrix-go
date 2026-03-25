import { useMemo } from 'react'
import type { FilteredStock } from '../types'

interface DepthStripProps {
  stocks: FilteredStock[]
}

const TIER_CONFIG = {
  D50: { color: '#ef4444', label: 'D50' },
  D30: { color: '#f59e0b', label: 'D30' },
  D5: { color: '#3b82f6', label: 'D5' },
  Eligible: { color: '#10b981', label: 'Eligible' },
} as const

export function DepthStrip({ stocks }: DepthStripProps) {
  const counts = useMemo(() => {
    const d50 = stocks.filter((s) => s.tier === 'D50').length
    const d30 = stocks.filter((s) => s.tier === 'D30').length
    const d5 = stocks.filter((s) => s.tier === 'D5').length
    const eligible = stocks.filter((s) => s.tier === 'Eligible').length
    const total = d50 + d30 + d5 + eligible
    return { D50: d50, D30: d30, D5: d5, Eligible: eligible, total }
  }, [stocks])

  if (counts.total === 0) {
    return (
      <div className='flex h-10 items-center justify-center rounded-lg border border-border/50 bg-card/50 text-xs text-muted-foreground'>
        No stocks pass filters
      </div>
    )
  }

  const tiers = (['D50', 'D30', 'D5', 'Eligible'] as const).filter((t) => counts[t] > 0)

  return (
    <div className='flex h-10 overflow-hidden rounded-lg'>
      {tiers.map((tier) => {
        const pct = (counts[tier] / counts.total) * 100
        return (
          <div
            key={tier}
            className='flex items-center justify-center transition-all duration-300'
            style={{
              width: `${pct}%`,
              backgroundColor: TIER_CONFIG[tier].color,
              minWidth: counts[tier] > 0 ? '48px' : 0,
            }}
          >
            <span className='text-xs font-semibold text-white drop-shadow'>
              {TIER_CONFIG[tier].label} ({counts[tier]})
            </span>
          </div>
        )
      })}
    </div>
  )
}
