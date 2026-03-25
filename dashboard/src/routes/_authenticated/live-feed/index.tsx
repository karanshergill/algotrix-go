import { createFileRoute } from '@tanstack/react-router'
import { LiveFeedPage } from '@/features/live-feed'

export const Route = createFileRoute('/_authenticated/live-feed/')({
  component: LiveFeedPage,
})
