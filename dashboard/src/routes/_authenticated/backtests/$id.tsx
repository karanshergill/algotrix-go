import { createFileRoute } from '@tanstack/react-router'
import { BacktestResultsPage } from '@/features/backtests'

export const Route = createFileRoute('/_authenticated/backtests/$id')({
  component: BacktestResultsPage,
})
