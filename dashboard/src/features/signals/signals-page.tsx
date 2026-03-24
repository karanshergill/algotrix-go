import { useState } from 'react'
import { format } from 'date-fns'
import { Zap } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { getISTDate } from '@/lib/market-hours'
import { DateNavigator } from '@/components/date-navigator'
import { formatScreenerName, formatSignalType, screenerColorClass } from '@/lib/format-signal'
import { useSignals, useSignalSummary } from './use-signals'

const SIGNAL_COLORS: Record<string, string> = {
  buy: 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10',
  alert: 'text-yellow-500 border-yellow-500/30 bg-yellow-500/10',
  breakout: 'text-blue-500 border-blue-500/30 bg-blue-500/10',
}

function signalBadge(type: string) {
  const cls = SIGNAL_COLORS[type.toLowerCase()] ?? 'text-muted-foreground border-border'
  return (
    <Badge variant='outline' className={cls}>
      {formatSignalType(type)}
    </Badge>
  )
}

export function SignalsPage() {
  const today = getISTDate()
  const [date, setDate] = useState(today)
  const [screenerFilter, setScreenerFilter] = useState<string>('')
  const [typeFilter, setTypeFilter] = useState<string>('')

  const { data: signals, isLoading } = useSignals(
    date,
    screenerFilter || undefined,
    typeFilter || undefined
  )
  const { data: summary } = useSignalSummary(date)

  const screeners = summary?.map((s) => s.screener_name) ?? []
  const signalTypes = [...new Set(signals?.map((s) => s.signal_type) ?? [])]
  const totalCount = summary?.reduce((a, s) => a + s.count, 0) ?? 0

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-3 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-1.5 rounded-lg bg-primary/10'>
            <Zap size={16} className='text-primary' />
          </div>
          <div>
            <h1 className='text-base font-semibold leading-tight'>Signals</h1>
            <p className='text-[10px] text-muted-foreground'>
              Screener signals fired during live trading
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Filters */}
      <div className='flex items-center gap-3 px-6 py-2 border-b border-border/50 shrink-0'>
        <DateNavigator value={date} onChange={setDate} />
        <select
          value={screenerFilter}
          onChange={(e) => setScreenerFilter(e.target.value)}
          className='h-7 rounded-md border border-input bg-background px-2 text-xs'
        >
          <option value=''>All Screeners</option>
          {screeners.map((s) => (
            <option key={s} value={s}>
              {formatScreenerName(s)}
            </option>
          ))}
        </select>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className='h-7 rounded-md border border-input bg-background px-2 text-xs'
        >
          <option value=''>All Types</option>
          {signalTypes.map((t) => (
            <option key={t} value={t}>
              {t.toUpperCase()}
            </option>
          ))}
        </select>
        <span className='ml-auto text-xs text-muted-foreground'>
          {totalCount} signal{totalCount !== 1 ? 's' : ''} today
        </span>
      </div>

      {/* Content */}
      <div className='flex-1 overflow-auto'>
        <div className='px-6 py-4'>
          {/* Summary cards */}
          {summary && summary.length > 0 && (
            <div className='grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 mb-4'>
              {summary.map((s) => (
                <Card
                  key={formatScreenerName(s.screener_name)}
                  className='p-3 cursor-pointer hover:bg-muted/20 transition-colors'
                  onClick={() =>
                    setScreenerFilter(
                      screenerFilter === s.screener_name ? '' : s.screener_name
                    )
                  }
                >
                  <div className='text-[10px] text-muted-foreground truncate'>
                    {formatScreenerName(s.screener_name)}
                  </div>
                  <div className='text-xl font-bold tabular-nums mt-0.5'>
                    {s.count}
                  </div>
                </Card>
              ))}
            </div>
          )}

          {/* Table */}
          {isLoading && (
            <div className='space-y-2'>
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className='h-10 w-full rounded-lg' />
              ))}
            </div>
          )}

          {signals && signals.length === 0 && !isLoading && (
            <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
              No signals for {date}
            </div>
          )}

          {signals && signals.length > 0 && (
            <Card className='overflow-hidden'>
              <table className='w-full text-sm'>
                <thead>
                  <tr className='border-b border-border bg-muted/30'>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>
                      Time
                    </th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>
                      Screener
                    </th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>
                      Symbol
                    </th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>
                      Type
                    </th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>
                      LTP
                    </th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>
                      Trigger Price
                    </th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>
                      % Above
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {signals.map((sig) => (
                    <tr
                      key={sig.id}
                      className='border-b border-border/50 hover:bg-muted/20 transition-colors'
                    >
                      <td className='px-4 py-2.5 tabular-nums text-xs'>
                        {format(new Date(sig.triggered_at), 'HH:mm:ss')}
                      </td>
                      <td className='px-4 py-2.5'>
                        <Badge variant='outline' className={`text-xs font-medium ${screenerColorClass(sig.screener_name)}`}>
                          {formatScreenerName(sig.screener_name)}
                        </Badge>
                      </td>
                      <td className='px-4 py-2.5 font-medium'>
                        {sig.trading_symbol}
                      </td>
                      <td className='px-4 py-2.5'>{signalBadge(sig.signal_type)}</td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        {Number(sig.ltp).toFixed(2)}
                      </td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        {Number(sig.trigger_price).toFixed(2)}
                      </td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        <span
                          className={
                            sig.percent_above > 0
                              ? 'text-emerald-500'
                              : 'text-red-400'
                          }
                        >
                          {Number(sig.percent_above).toFixed(2)}%
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
