import { useQuery } from '@tanstack/react-query'

export interface CalendarDay {
  date: string
  is_trading_day: boolean
  is_weekend: boolean
  is_holiday: boolean
  holiday_name: string | null
  is_muhurat: boolean
  session_pre_open: string | null
  session_open: string | null
  session_close: string | null
  session_post_close: string | null
}

interface RawCalendarDay {
  date: string
  is_trading_day: boolean
  holiday_name: string | null
  pre_open_start: string | null
  exchange_open: string | null
  exchange_close: string | null
  post_close_end: string | null
  is_muhurat: boolean
  notes: string | null
}

function parseRow(raw: RawCalendarDay): CalendarDay {
  const date = new Date(raw.date)
  const dayOfWeek = date.getUTCDay()
  const isWeekend = dayOfWeek === 0 || dayOfWeek === 6
  const isHoliday =
    raw.holiday_name !== null &&
    raw.holiday_name !== '' &&
    raw.holiday_name !== 'Weekend'

  return {
    date: raw.date,
    is_trading_day: raw.is_trading_day,
    is_weekend: isWeekend,
    is_holiday: isHoliday,
    holiday_name: isHoliday ? raw.holiday_name : null,
    is_muhurat: raw.is_muhurat ?? false,
    session_pre_open: raw.pre_open_start ?? null,
    session_open: raw.exchange_open ?? null,
    session_close: raw.exchange_close ?? null,
    session_post_close: raw.post_close_end ?? null,
  }
}

export function useCalendarData(year: number, month: number) {
  const start = `${year}-${String(month + 1).padStart(2, '0')}-01`
  const endMonth = month === 11 ? 0 : month + 1
  const endYear = month === 11 ? year + 1 : year
  const end = `${endYear}-${String(endMonth + 1).padStart(2, '0')}-01`

  return useQuery<CalendarDay[]>({
    queryKey: ['exchange-calendar', year, month],
    queryFn: async () => {
      const res = await fetch(`/api/calendar?from=${start}&to=${end}`)
      if (!res.ok) throw new Error('Failed to fetch calendar data')
      const rows: RawCalendarDay[] = await res.json()
      return rows.map(parseRow)
    },
    staleTime: 5 * 60 * 1000,
  })
}

export function useUpcomingHolidays() {
  return useQuery<CalendarDay[]>({
    queryKey: ['upcoming-holidays'],
    queryFn: async () => {
      const res = await fetch('/api/calendar/upcoming-holidays')
      if (!res.ok) throw new Error('Failed to fetch upcoming holidays')
      const rows: RawCalendarDay[] = await res.json()
      return rows.map(parseRow)
    },
    staleTime: 5 * 60 * 1000,
  })
}
