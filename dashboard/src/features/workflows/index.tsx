import { ReactFlowProvider } from '@xyflow/react'
import { NodePalette } from './components/node-palette'
import { WorkflowCanvas } from './components/workflow-canvas'

export function WorkflowBuilderPage() {
  return (
    <ReactFlowProvider>
      <div className='flex h-[calc(100vh-theme(spacing.16))] overflow-hidden'>
        <NodePalette />
        <div className='flex-1 relative'>
          <div className='absolute inset-x-0 top-0 z-10 flex items-center justify-between border-b bg-background/80 backdrop-blur-sm px-4 py-2'>
            <div>
              <h2 className='text-lg font-semibold'>Workflow Builder</h2>
              <p className='text-xs text-muted-foreground'>
                Drag nodes from the palette, connect them to build watchlist pipelines
              </p>
            </div>
          </div>
          <div className='h-full pt-14'>
            <WorkflowCanvas />
          </div>
        </div>
      </div>
    </ReactFlowProvider>
  )
}
