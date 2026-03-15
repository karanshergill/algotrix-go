import { createFileRoute } from '@tanstack/react-router'
import { WorkflowBuilderPage } from '@/features/workflows'

export const Route = createFileRoute('/_authenticated/workflows/')({
  component: WorkflowBuilderPage,
})
