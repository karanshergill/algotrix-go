import { useMemo, useState } from 'react'
import {
  AlertCircle,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,

  PartyPopper,
  TrendingUp,
} from 'lucide-react'
import { toast } from 'sonner'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { OhlcvDayCell, type CalendarDayType } from './ohlcv-day-cell'
import { OhlcvDayDetail } from './ohlcv-day-detail'
import { OhlcvFetchProgress } from './ohlcv-fetch-progress'
import { OhlcvSymbolSheet } from './ohlcv-symbol-sheet'
import { RESOLUTIONS, type Resolution } from './constants'
import { createMissingDayStatus } from './types'
import {
  useCalendarData,
  useUpcomingHolidays,
  type CalendarDay,
} from './use-calendar-data'
import { useOhlcvFetch } from './use-ohlcv-fetch'
import { useOhlcvStatus } from './use-ohlcv-status'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
const MONTH_NAMES = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]

type SymbolSheetState = {
  date: string
  resolution: Resolution
} | null

export function OhlcvCalendarPage() {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth())
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [startingFetch, setStartingFetch] = useState<Resolution | 'all' | null>(
    null
  )
  const [sheetState, setSheetState] = useState<SymbolSheetState>(null)
  const [activeJobIds, setActiveJobIds] = useState<string[]>([])

  const monthStart = formatDate(year, month, 1)
  const monthEnd = formatDate(year, month, new Date(year, month + 1, 0).getDate())

  const { data: days, isLoading, isFetching, error } = useCalendarData(year, month)
  const { data: upcomingHolidays } = useUpcomingHolidays()
  const { data: ohlcvStatus, isLoading: ohlcvStatusLoading, isFetching: ohlcvFetching } = useOhlcvStatus(monthStart, monthEnd)
  const startFetch = useOhlcvFetch()

  const dayMap = useMemo(() => {
    const map = new Map<string, CalendarDay>()
    days?.forEach((day) => {
      map.set(day.date.slice(0, 10), day)
    })
    return map
  }, [days])

  const cells = getMonthGrid(year, month)

  const stats = useMemo(() => {
    if (!days) {
      return {
        trading: 0,
        holidays: 0,
        weekends: 0,
        nextHoliday: null as CalendarDay | null,
        daysUntilHoliday: 0,
      }
    }

    const trading = days.filter((day) => day.is_trading_day || day.is_muhurat).length
    const holidays = days.filter((day) => day.is_holiday).length
    const weekends = days.filter(
      (day) => day.is_weekend && !day.is_holiday
    ).length
    const nextHoliday = upcomingHolidays?.[0] ?? null
    const daysUntilHoliday = nextHoliday
      ? getDaysUntil(nextHoliday.date.slice(0, 10))
      : 0

    return {
      trading,
      holidays,
      weekends,
      nextHoliday,
      daysUntilHoliday,
    }
  }, [days, upcomingHolidays])

  const selectedDay = selectedDate ? dayMap.get(selectedDate) ?? null : null
  const selectedDayType = selectedDay ? getDayType(selectedDay) : null
  const selectedCoverage = selectedDate
    ? ohlcvStatus?.days[selectedDate] ?? createMissingDayStatus()
    : null

  function prevMonth() {
    if (month === 0) {
      setMonth(11)
      setYear(year - 1)
    } else {
      setMonth(month - 1)
    }
    setSelectedDate(null)
  }

  function nextMonth() {
    if (month === 11) {
      setMonth(0)
      setYear(year + 1)
    } else {
      setMonth(month + 1)
    }
    setSelectedDate(null)
  }

  function goToday() {
    setYear(now.getFullYear())
    setMonth(now.getMonth())
    setSelectedDate(null)
  }

  async function handleFetch(resolution: Resolution) {
    if (!selectedDate) {
      return
    }

    setStartingFetch(resolution)
    try {
      const job = await startFetch.mutateAsync({
        resolution,
        from: selectedDate,
        to: selectedDate,
      })
      setActiveJobIds((current) => [...new Set([...current, job.jobId])])
    } catch (fetchError) {
      toast.error(
        fetchError instanceof Error
          ? fetchError.message
          : 'Failed to start OHLCV fetch'
      )
    } finally {
      setStartingFetch(null)
    }
  }

  async function handleFetchAll() {
    if (!selectedDate) {
      return
    }

    setStartingFetch('all')
    try {
      const jobs = await Promise.all(
        RESOLUTIONS.map((resolution) =>
          startFetch.mutateAsync({
            resolution,
            from: selectedDate,
            to: selectedDate,
          })
        )
      )

      setActiveJobIds((current) => [
        ...new Set([...current, ...jobs.map((job) => job.jobId)]),
      ])
    } catch (fetchError) {
      toast.error(
        fetchError instanceof Error
          ? fetchError.message
          : 'Failed to start OHLCV fetch'
      )
    } finally {
      setStartingFetch(null)
    }
  }

  function handleViewSymbols(resolution: Resolution) {
    if (!selectedDate) {
      return
    }

    setSheetState({
      date: selectedDate,
      resolution,
    })
  }

  return (
    <>
      <Header>
        <HeaderToolbar />
      </Header>

      <Main>
        <div className='mb-4 flex items-center justify-between'>
          <h1 className='text-2xl font-bold tracking-tight'>OHLCV Data</h1>
        </div>

        <div className='mb-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4'>
          <StatCard
            title='Trading Days'
            value={stats.trading}
            icon={<TrendingUp className='size-5 text-green-500' />}
            iconClassName='bg-green-500/10'
          />
          <StatCard
            title='Holidays'
            value={stats.holidays}
            icon={<PartyPopper className='size-5 text-red-500' />}
            iconClassName='bg-red-500/10'
          />
          <StatCard
            title='Weekends'
            value={stats.weekends}
            icon={<CalendarDays className='size-5 text-muted-foreground' />}
            iconClassName='bg-muted'
          />
          <Card className='py-4'>
            <CardContent className='flex items-center gap-3 px-4'>
              <div className='flex size-9 items-center justify-center rounded-lg bg-orange-500/10'>
                <Clock className='size-5 text-orange-500' />
              </div>
              <div>
                <p className='text-sm text-muted-foreground'>Next Holiday</p>
                {stats.nextHoliday ? (
                  <>
                    <p className='truncate text-sm font-semibold'>
                      {stats.nextHoliday.holiday_name}
                    </p>
                    <p className='text-xs text-muted-foreground'>
                      {stats.daysUntilHoliday === 0
                        ? 'Today'
                        : stats.daysUntilHoliday === 1
                          ? 'Tomorrow'
                          : `in ${stats.daysUntilHoliday} days`}
                    </p>
                  </>
                ) : (
                  <p className='text-sm font-semibold text-muted-foreground'>
                    None upcoming
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        <div className='grid grid-cols-1 gap-4 lg:grid-cols-3'>
          <Card className='lg:col-span-2'>
            <CardHeader className='flex-row items-center justify-between pb-2'>
              <CardTitle className='text-lg'>
                {MONTH_NAMES[month]} {year}
              </CardTitle>
              <div className='flex items-center gap-1'>
                <Button variant='outline' size='icon' onClick={prevMonth}>
                  <ChevronLeft className='size-4' />
                </Button>
                <Button variant='outline' size='sm' onClick={goToday}>
                  Today
                </Button>
                <Button variant='outline' size='icon' onClick={nextMonth}>
                  <ChevronRight className='size-4' />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {error ? (
                <div className='flex h-72 flex-col items-center justify-center gap-2 text-muted-foreground'>
                  <AlertCircle className='size-6' />
                  <p className='text-sm'>Failed to load calendar data</p>
                </div>
              ) : (
                <>
                  <div className='grid grid-cols-5 gap-2'>
                    {WEEKDAYS.map((weekday) => (
                      <div
                        key={weekday}
                        className='py-2 text-center text-xs font-medium text-muted-foreground'
                      >
                        {weekday}
                      </div>
                    ))}
                  </div>

                  <div className='grid grid-cols-5 gap-2'>
                    {cells.map((day, index) => {
                      if (day === null) {
                        return <div key={`empty-${index}`} className='h-24' />
                      }

                      const date = formatDate(year, month, day)

                      // Show skeleton while calendar data is loading
                      if (isLoading || isFetching) {
                        return (
                          <div
                            key={date}
                            className='h-24 animate-pulse rounded-lg border bg-muted/40'
                          />
                        )
                      }

                      const calendarDay = dayMap.get(date)
                      const dayType = calendarDay
                        ? getDayType(calendarDay)
                        : 'trading'
                      const coverage =
                        ohlcvStatus?.days[date] ?? createMissingDayStatus()

                      return (
                        <OhlcvDayCell
                          key={date}
                          day={day}
                          date={date}
                          dayType={dayType}
                          holidayName={calendarDay?.holiday_name}
                          today={isTodayDate(year, month, day)}
                          selected={selectedDate === date}
                          totalSymbols={ohlcvStatus?.totalSymbols ?? 0}
                          coverage={coverage}
                          ohlcvLoading={(ohlcvStatusLoading || ohlcvFetching) && !ohlcvStatus?.days[date]}
                          onSelect={setSelectedDate}
                        />
                      )
                    })}
                  </div>

                  <div className='mt-4 flex flex-wrap items-center gap-4 text-xs text-muted-foreground'>
                    <span className='flex items-center gap-1.5'>
                      <span className='size-2 rounded-full bg-green-500' />
                      Full
                    </span>
                    <span className='flex items-center gap-1.5'>
                      <span className='size-2 rounded-full bg-amber-500' />
                      Partial
                    </span>
                    <span className='flex items-center gap-1.5'>
                      <span className='size-2 rounded-full bg-muted-foreground/40' />
                      Missing
                    </span>
                    <span className='flex items-center gap-1.5'>
                      <Badge
                        variant='destructive'
                        className='h-4 px-1 text-[10px]'
                      >
                        H
                      </Badge>
                      Holiday
                    </span>
                    <span className='flex items-center gap-1.5'>
                      <span className='size-2 rounded-full ring-2 ring-blue-500' />
                      Today
                    </span>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <div className='flex flex-col gap-4'>
            <Card>
              <CardHeader>
                <CardTitle className='text-base'>Day Details</CardTitle>
              </CardHeader>
              <CardContent>
                <OhlcvDayDetail
                  date={selectedDate}
                  day={selectedDay}
                  dayType={selectedDayType}
                  coverage={selectedCoverage}
                  totalSymbols={ohlcvStatus?.totalSymbols ?? 0}
                  isStartingFetch={startingFetch !== null}
                  onFetch={handleFetch}
                  onFetchAll={handleFetchAll}
                  onViewSymbols={handleViewSymbols}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className='text-base'>Upcoming Holidays</CardTitle>
              </CardHeader>
              <CardContent>
                {upcomingHolidays && upcomingHolidays.length > 0 ? (
                  <ul className='space-y-3'>
                    {upcomingHolidays.map((holiday) => {
                      const daysAway = getDaysUntil(holiday.date.slice(0, 10))
                      return (
                        <li
                          key={holiday.date}
                          className='flex items-start justify-between gap-2'
                        >
                          <div>
                            <p className='text-sm font-medium'>
                              {holiday.holiday_name}
                            </p>
                            <p className='text-xs text-muted-foreground'>
                              {new Date(holiday.date).toLocaleDateString('en-IN', {
                                weekday: 'short',
                                day: 'numeric',
                                month: 'short',
                              })}
                              {daysAway > 0 && (
                                <span className='ml-1'>&middot; {daysAway}d away</span>
                              )}
                            </p>
                          </div>
                          <Badge variant='destructive' className='shrink-0'>
                            Holiday
                          </Badge>
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <p className='text-sm text-muted-foreground'>
                    No upcoming holidays
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </div>

        <div className='pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-3'>
          {activeJobIds.map((jobId) => (
            <div key={jobId} className='pointer-events-auto'>
              <OhlcvFetchProgress
                jobId={jobId}
                onDismiss={(dismissedJobId) => {
                  setActiveJobIds((current) =>
                    current.filter((currentJobId) => currentJobId !== dismissedJobId)
                  )
                }}
              />
            </div>
          ))}
        </div>
      </Main>

      <OhlcvSymbolSheet
        open={sheetState !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSheetState(null)
          }
        }}
        date={sheetState?.date ?? null}
        resolution={sheetState?.resolution ?? null}
      />
    </>
  )
}

function StatCard({
  title,
  value,
  icon,
  iconClassName,
}: {
  title: string
  value: number
  icon: React.ReactNode
  iconClassName: string
}) {
  return (
    <Card className='py-4'>
      <CardContent className='flex items-center gap-3 px-4'>
        <div className={cn('flex size-9 items-center justify-center rounded-lg', iconClassName)}>
          {icon}
        </div>
        <div>
          <p className='text-sm text-muted-foreground'>{title}</p>
          <p className='text-xl font-bold'>{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}

// Returns only Mon-Fri cells (null for padding, number for day)
// Weekends are completely excluded
function getMonthGrid(year: number, month: number) {
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = []

  // Find the weekday (0=Mon..4=Fri) of the 1st
  const firstDay = new Date(year, month, 1)
  const firstDow = firstDay.getDay() // 0=Sun,1=Mon...6=Sat
  // Add leading nulls for Mon-Fri offset
  const leadingNulls = firstDow === 0 ? 4 : Math.min(firstDow - 1, 4)
  for (let i = 0; i < leadingNulls; i++) cells.push(null)

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(year, month, day)
    const dow = date.getDay()
    if (dow !== 0 && dow !== 6) {
      // Mon-Fri only
      cells.push(day)
    }
  }

  // Pad to complete last row of 5
  while (cells.length % 5 !== 0) cells.push(null)

  return cells
}

function formatDate(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function isTodayDate(year: number, month: number, day: number) {
  const now = new Date()
  return (
    now.getFullYear() === year &&
    now.getMonth() === month &&
    now.getDate() === day
  )
}

function getDaysUntil(targetDate: string): number {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(`${targetDate}T00:00:00`)
  target.setHours(0, 0, 0, 0)
  return Math.ceil((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

function getDayType(day: CalendarDay): CalendarDayType {
  if (day.is_muhurat) {
    return 'muhurat'
  }

  if (day.is_holiday) {
    return 'holiday'
  }

  if (day.is_weekend) {
    return 'weekend'
  }

  return 'trading'
}
