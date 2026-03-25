import { createFileRoute } from '@tanstack/react-router'
import { UniverseExplorerPage } from '@/features/universe'

export const Route = createFileRoute('/_authenticated/universe/')({
  component: UniverseExplorerPage,
})
