import { createFileRoute } from '@tanstack/react-router'
import { BacktestsListPage } from '@/features/backtests'

export const Route = createFileRoute('/_authenticated/backtests/')({
  component: BacktestsListPage,
})
