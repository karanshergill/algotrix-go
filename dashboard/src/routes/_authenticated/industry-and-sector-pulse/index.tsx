import { createFileRoute } from '@tanstack/react-router'
import { SectorPulsePage } from '@/features/sectors'

export const Route = createFileRoute('/_authenticated/industry-and-sector-pulse/')({
  component: SectorPulsePage,
})
