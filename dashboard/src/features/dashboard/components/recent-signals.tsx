import { useState } from 'react'
import { format } from 'date-fns'
import { Moon } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { isMarketOpen, getISTDate } from '@/lib/market-hours'
import { formatScreenerName, formatSignalType, screenerColorClass } from '@/lib/format-signal'
import { useSignalSummary } from '@/features/signals/use-signals'
import type { Signal, SignalSummary } from '@/features/signals/types'
import { useQuery } from '@tanstack/react-query'

const SIGNAL_COLORS: Record<string, string> = {
  buy: 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10',
  alert: 'text-yellow-500 border-yellow-500/30 bg-yellow-500/10',
  breakout: 'text-blue-500 border-blue-500/30 bg-blue-500/10',
}

async function fetchRecentSignals(date: string): Promise<Signal[]> {
  const res = await fetch(`/api/signals?date=${date}&limit=15`)
  if (!res.ok) throw new Error('Failed to fetch signals')
  return res.json()
}

export function RecentSignals() {
  const today = getISTDate()
  const marketOpen = isMarketOpen()
  const [activeScreener, setActiveScreener] = useState<string | null>(null)

  const { data: summary } = useSignalSummary(today)
  const { data: signals, isLoading } = useQuery({
    queryKey: ['recent-signals', today],
    queryFn: () => fetchRecentSignals(today),
    refetchInterval: marketOpen ? 10_000 : false,
  })

  const totalCount = summary?.reduce((a, s) => a + s.count, 0) ?? 0
  const filtered = activeScreener
    ? signals?.filter((s) => s.screener_name === activeScreener)
    : signals

  return (
    <div className='space-y-3'>
      {/* Market closed indicator */}
      {!marketOpen && (
        <div className='flex items-center gap-1.5 text-[10px] text-muted-foreground'>
          <Moon size={10} />
          Market closed — showing last session
        </div>
      )}

      {/* Filter chips */}
      {summary && summary.length > 0 && (
        <div className='flex flex-wrap gap-1.5'>
          <button
            onClick={() => setActiveScreener(null)}
            className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-medium transition-colors ${
              activeScreener === null
                ? 'border-primary bg-primary/10 text-primary'
                : 'border-border text-muted-foreground hover:text-foreground'
            }`}
          >
            All ({totalCount})
          </button>
          {summary.map((s: SignalSummary) => (
            <button
              key={s.screener_name}
              onClick={() =>
                setActiveScreener(
                  activeScreener === s.screener_name ? null : s.screener_name
                )
              }
              className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                activeScreener === s.screener_name
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border text-muted-foreground hover:text-foreground'
              }`}
            >
              {formatScreenerName(s.screener_name)} ({s.count})
            </button>
          ))}
        </div>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div className='space-y-2'>
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className='h-8 w-full rounded' />
          ))}
        </div>
      )}

      {/* Empty state */}
      {filtered && filtered.length === 0 && !isLoading && (
        <div className='flex items-center justify-center h-24 text-muted-foreground text-xs'>
          No signals today
        </div>
      )}

      {/* Signal rows */}
      {filtered && filtered.length > 0 && (
        <div className='space-y-1'>
          {filtered.map((sig) => {
            const typeLower = sig.signal_type.toLowerCase()
            const typeColor =
              SIGNAL_COLORS[typeLower] ?? 'text-muted-foreground border-border'
            return (
              <div
                key={sig.id}
                className='flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-muted/20 transition-colors'
              >
                <span className='text-xs tabular-nums font-semibold text-foreground shrink-0'>
                  {format(new Date(sig.triggered_at), 'HH:mm:ss')}
                </span>
                <Badge variant='outline' className={`text-[11px] font-medium shrink-0 ${screenerColorClass(sig.screener_name)}`}>
                  {formatScreenerName(sig.screener_name)}
                </Badge>
                <span className='text-xs font-medium truncate'>
                  {sig.trading_symbol}
                </span>
                <Badge variant='outline' className={`text-[10px] shrink-0 ${typeColor}`}>
                  {formatSignalType(sig.signal_type)}
                </Badge>
                <span className='ml-auto text-xs tabular-nums shrink-0'>
                  {Number(sig.ltp).toFixed(2)}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function useRecentSignalCount(): number {
  const today = getISTDate()
  const { data: summary } = useSignalSummary(today)
  return summary?.reduce((a, s) => a + s.count, 0) ?? 0
}
