import { createFileRoute } from '@tanstack/react-router'
import { SignalsPage } from '@/features/signals'

export const Route = createFileRoute('/_authenticated/signals/')({
  component: SignalsPage,
})
