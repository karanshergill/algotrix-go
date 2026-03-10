import { ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { RESOLUTIONS, RESOLUTION_LABELS, type Resolution } from './constants'
import type { CalendarDay } from './use-calendar-data'
import {
  getCoveragePercent,
  type CoverageStatus,
  type DayOhlcvStatus,
} from './types'
import type { CalendarDayType } from './ohlcv-day-cell'

interface OhlcvDayDetailProps {
  date: string | null
  day: CalendarDay | null
  dayType: CalendarDayType | null
  coverage: DayOhlcvStatus | null
  totalSymbols: number
  isStartingFetch: boolean
  onFetch: (resolution: Resolution) => void
  onFetchAll: () => void
  onViewSymbols: (resolution: Resolution) => void
}

export function OhlcvDayDetail({
  date,
  day,
  dayType,
  coverage,
  totalSymbols,
  isStartingFetch,
  onFetch,
  onFetchAll,
  onViewSymbols,
}: OhlcvDayDetailProps) {
  if (!date || !coverage || !dayType) {
    return (
      <p className='text-sm text-muted-foreground'>
        Click a day to inspect OHLCV coverage.
      </p>
    )
  }

  const hasSession = day?.is_trading_day || day?.is_muhurat

  return (
    <div className='space-y-4'>
      <div>
        <h3 className='text-base font-semibold'>
          {new Date(`${date}T00:00:00`).toLocaleDateString('en-IN', {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
            year: 'numeric',
          })}
        </h3>
        <div className='mt-2 flex flex-wrap items-center gap-2'>
          <Badge
            variant={
              dayType === 'holiday'
                ? 'destructive'
                : dayType === 'weekend'
                  ? 'secondary'
                  : 'default'
            }
            className={cn(
              dayType === 'muhurat' &&
                'border-amber-500/60 bg-amber-500/10 text-amber-500'
            )}
          >
            {dayType === 'muhurat'
              ? 'Muhurat Trading'
              : dayType.charAt(0).toUpperCase() + dayType.slice(1)}
          </Badge>
          {day?.holiday_name && (
            <Badge variant='outline'>{day.holiday_name}</Badge>
          )}
        </div>
      </div>

      {hasSession && (
        <div className='space-y-2'>
          <p className='text-sm font-medium text-muted-foreground'>
            Session Timings
          </p>
          <div className='space-y-1 text-sm'>
            {day?.session_pre_open && (
              <SessionRow label='Pre-Open' value={day.session_pre_open} />
            )}
            {day?.session_open && (
              <SessionRow label='Open' value={day.session_open} />
            )}
            {day?.session_close && (
              <SessionRow label='Close' value={day.session_close} />
            )}
            {day?.session_post_close && (
              <SessionRow label='Post-Close' value={day.session_post_close} />
            )}
          </div>
        </div>
      )}

      <div className='space-y-3'>
        {RESOLUTIONS.map((resolution) => {
          const item = coverage[resolution]
          const percent = getCoveragePercent(item.count, totalSymbols)

          return (
            <div key={resolution} className='rounded-lg border p-3'>
              <div className='flex items-start justify-between gap-3'>
                <div>
                  <div className='flex items-center gap-2'>
                    <span className='font-medium'>
                      {RESOLUTION_LABELS[resolution]}
                    </span>
                    <Badge
                      variant={item.status === 'missing' ? 'secondary' : 'outline'}
                      className={cn(statusClassName(item.status))}
                    >
                      {item.status}
                    </Badge>
                  </div>
                  <p className='mt-1 text-xs text-muted-foreground'>
                    {item.count.toLocaleString()} /{' '}
                    {totalSymbols.toLocaleString()} symbols
                  </p>
                </div>
                <span className='text-sm font-semibold'>{percent}%</span>
              </div>

              <div className='mt-3 h-2 overflow-hidden rounded-full bg-muted'>
                <div
                  className={cn(
                    'h-full rounded-full transition-[width]',
                    progressBarClassName(item.status)
                  )}
                  style={{ width: `${percent}%` }}
                />
              </div>

              <div className='mt-3 flex flex-wrap gap-2'>
                <Button
                  size='sm'
                  variant='outline'
                  onClick={() => onFetch(resolution)}
                  disabled={isStartingFetch}
                >
                  Fetch {RESOLUTION_LABELS[resolution]}
                </Button>
                <Button
                  size='sm'
                  variant='ghost'
                  onClick={() => onViewSymbols(resolution)}
                >
                  View symbols
                  <ArrowRight className='size-4' />
                </Button>
              </div>
            </div>
          )
        })}
      </div>

      <Button className='w-full' onClick={onFetchAll} disabled={isStartingFetch}>
        Fetch All
      </Button>
    </div>
  )
}

function SessionRow({ label, value }: { label: string; value: string }) {
  return (
    <div className='flex items-center justify-between'>
      <span className='text-muted-foreground'>{label}</span>
      <span className='font-medium'>{value}</span>
    </div>
  )
}

function statusClassName(status: CoverageStatus): string {
  if (status === 'full') {
    return 'border-emerald-500/30 text-emerald-500'
  }

  if (status === 'partial') {
    return 'border-amber-500/30 text-amber-500'
  }

  return 'text-muted-foreground'
}

function progressBarClassName(status: CoverageStatus): string {
  if (status === 'full') {
    return 'bg-emerald-500'
  }

  if (status === 'partial') {
    return 'bg-amber-500'
  }

  return 'bg-muted-foreground/40'
}
