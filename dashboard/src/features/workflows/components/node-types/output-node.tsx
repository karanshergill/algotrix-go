import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { WorkflowNode } from '../../types'
import { CATEGORY_COLORS } from '../../types'

export function OutputNode({ data, selected }: NodeProps<WorkflowNode>) {
  const color = CATEGORY_COLORS.output

  return (
    <div
      className={`min-w-[180px] rounded-lg border bg-card shadow-md transition-shadow ${selected ? 'ring-2 ring-ring shadow-lg' : ''}`}
    >
      <div
        className='flex items-center gap-2 rounded-t-lg px-3 py-2 text-white text-sm font-medium'
        style={{ backgroundColor: color }}
      >
        <span>{data.icon}</span>
        <span>{data.label}</span>
      </div>
      <div className='px-3 py-2 text-xs text-muted-foreground'>
        {data.description}
      </div>
      <Handle
        type='target'
        position={Position.Left}
        className='!w-3 !h-3 !border-2 !border-background'
        style={{ backgroundColor: color }}
      />
    </div>
  )
}
