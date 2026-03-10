import { Badge } from '@/components/ui/badge'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { RESOLUTIONS } from './constants'
import type { CoverageStatus, DayOhlcvStatus } from './types'

export type CalendarDayType = 'trading' | 'holiday' | 'weekend' | 'muhurat'

interface OhlcvDayCellProps {
  day: number
  date: string
  dayType: CalendarDayType
  holidayName?: string | null
  today: boolean
  selected: boolean
  totalSymbols: number
  coverage: DayOhlcvStatus
  ohlcvLoading?: boolean
  onSelect: (date: string) => void
}

export function OhlcvDayCell({
  day,
  date,
  dayType,
  holidayName,
  today,
  selected,
  totalSymbols,
  coverage,
  ohlcvLoading = false,
  onSelect,
}: OhlcvDayCellProps) {
  const isWeekend = dayType === 'weekend'
  const isHoliday = dayType === 'holiday'
  const isMuhurat = dayType === 'muhurat'

  return (
    <button
      onClick={() => onSelect(date)}
      className={cn(
        'relative flex h-24 flex-col items-start rounded-lg border p-2 text-left transition-colors hover:bg-accent',
        isWeekend && 'bg-muted/50 text-muted-foreground',
        isHoliday && 'bg-red-500/5',
        isMuhurat && 'border-amber-500/60',
        today && 'ring-2 ring-blue-500',
        selected && 'bg-accent'
      )}
    >
      <span
        className={cn(
          'text-sm font-medium',
          isHoliday && 'text-red-500',
          today && 'font-bold'
        )}
      >
        {day}
      </span>

      <div className='mt-2 flex w-full flex-col gap-1'>
        {(dayType === 'trading' || dayType === 'muhurat') && ohlcvLoading && (
          <>
            {RESOLUTIONS.map((r) => (
              <div key={r} className='h-3 animate-pulse rounded bg-muted/60' />
            ))}
          </>
        )}
        {(dayType === 'trading' || dayType === 'muhurat') && !ohlcvLoading && RESOLUTIONS.map((resolution) => {
          const value = coverage[resolution]
          return (
            <Tooltip key={resolution}>
              <TooltipTrigger asChild>
                <span className='flex items-center justify-between text-[10px] leading-none'>
                  <span className='uppercase text-muted-foreground'>
                    {resolution}
                  </span>
                  <span className='flex items-center gap-1 tabular-nums'>
                    {value.status === 'missing' ? (
                      <span className='text-muted-foreground/40'>—</span>
                    ) : (
                      <>
                        <span
                          className={cn(
                            'size-1.5 rounded-full',
                            indicatorColor(value.status)
                          )}
                        />
                        <span>{value.count}</span>
                      </>
                    )}
                  </span>
                </span>
              </TooltipTrigger>
              <TooltipContent side='top'>
                {resolution}: {value.count}/{totalSymbols} symbols
              </TooltipContent>
            </Tooltip>
          )
        })}
      </div>

      {isHoliday && (
        <span className='absolute right-2 top-2'>
          <Badge variant='destructive' className='h-4 px-1 text-[10px]'>
            H
          </Badge>
        </span>
      )}

      {isMuhurat && (
        <span className='absolute bottom-2 right-2 text-[10px] font-medium text-amber-500'>
          Muhurat
        </span>
      )}

      {holidayName && (
        <span className='mt-auto line-clamp-1 text-[10px] text-muted-foreground'>
          {holidayName}
        </span>
      )}
    </button>
  )
}

function indicatorColor(status: CoverageStatus): string {
  if (status === 'full') {
    return 'bg-emerald-500'
  }

  if (status === 'partial') {
    return 'bg-amber-500'
  }

  return 'bg-muted-foreground/40'
}
