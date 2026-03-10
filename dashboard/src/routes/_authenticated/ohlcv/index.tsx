import { createFileRoute } from '@tanstack/react-router'
import { OhlcvCalendarPage } from '@/features/ohlcv'

export const Route = createFileRoute('/_authenticated/ohlcv/')({
  component: OhlcvCalendarPage,
})
