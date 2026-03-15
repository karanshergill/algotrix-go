import { createFileRoute } from '@tanstack/react-router'
import { WatchlistBuilderPage } from '@/features/watchlist'

export const Route = createFileRoute('/_authenticated/watchlist/')({
  component: WatchlistBuilderPage,
})
