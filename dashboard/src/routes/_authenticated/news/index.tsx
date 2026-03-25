import { createFileRoute } from '@tanstack/react-router'
import { NewsPage } from '@/features/news'

export const Route = createFileRoute('/_authenticated/news/')({
  component: NewsPage,
})
