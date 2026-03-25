import { useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import type { FilteredStock } from '../types'

interface StatCardsProps {
  stocks: FilteredStock[]
  totalStocks: number
}

export function StatCards({ stocks, totalStocks }: StatCardsProps) {
  const stats = useMemo(() => {
    const eligible = stocks.filter((s) => s.pass).length
    const d50 = stocks.filter((s) => s.tier === 'D50').length
    const d30 = stocks.filter((s) => s.tier === 'D30').length
    const d5 = stocks.filter((s) => s.tier === 'D5').length
    const eligibleOnly = stocks.filter((s) => s.tier === 'Eligible').length
    return { eligible, d50, d30, d5, eligibleOnly }
  }, [stocks])

  const pct = totalStocks > 0 ? Math.round((stats.eligible / totalStocks) * 100) : 0

  return (
    <div className='grid grid-cols-3 gap-3'>
      {/* Card 1: Universe */}
      <Card className='bg-card/50'>
        <CardContent className='px-4 py-2'>
          <div className='flex items-center justify-between'>
            <p className='text-sm font-medium text-blue-400'>Universe</p>
            <p className='text-lg font-bold tabular-nums'>{totalStocks.toLocaleString()}</p>
          </div>
        </CardContent>
      </Card>

      {/* Card 2: Eligible */}
      <Card className='bg-card/50'>
        <CardContent className='px-4 py-2'>
          <div className='flex items-center justify-between'>
            <p className='text-sm font-medium text-emerald-400'>Eligible</p>
            <p className='text-lg font-bold tabular-nums text-emerald-400'>
              {stats.eligible.toLocaleString()}
              <span className='text-sm text-muted-foreground ml-1'>({pct}%)</span>
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Card 3: Depth Allocation (combined) */}
      <Card className='bg-card/50'>
        <CardContent className='px-4 py-2'>
          <div className='flex items-center justify-between gap-4'>
            <p className='text-sm font-medium text-amber-400'>Depth</p>
            <div className='flex items-center gap-3 text-xs tabular-nums'>
              <span>
                <span className='text-red-400 font-bold text-sm'>{stats.d50}</span>
                <span className='text-muted-foreground'>/5 D50</span>
              </span>
              <span className='text-muted-foreground/30'>│</span>
              <span>
                <span className='text-amber-400 font-bold text-sm'>{stats.d30}</span>
                <span className='text-muted-foreground'>/250 D30</span>
              </span>
              <span className='text-muted-foreground/30'>│</span>
              <span>
                <span className='text-blue-400 font-bold text-sm'>{stats.d5}</span>
                <span className='text-muted-foreground'>/200 D5</span>
              </span>
              <span className='text-muted-foreground/30'>│</span>
              <span>
                <span className='text-emerald-400 font-bold text-sm'>{stats.eligibleOnly}</span>
                <span className='text-muted-foreground'> elig</span>
              </span>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
