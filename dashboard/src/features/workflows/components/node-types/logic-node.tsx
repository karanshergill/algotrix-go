import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { WorkflowNode } from '../../types'
import { CATEGORY_COLORS } from '../../types'

export function LogicNode({ data, selected }: NodeProps<WorkflowNode>) {
  const color = CATEGORY_COLORS.logic
  const isMultiInput = ['AND (Intersection)', 'OR (Union)', 'NOT (Exclude)'].includes(data.label)

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
      {/* Multiple input handles for set operations */}
      <Handle
        type='target'
        position={Position.Left}
        id='a'
        className='!w-3 !h-3 !border-2 !border-background'
        style={{ backgroundColor: color, top: isMultiInput ? '35%' : '50%' }}
      />
      {isMultiInput && (
        <Handle
          type='target'
          position={Position.Left}
          id='b'
          className='!w-3 !h-3 !border-2 !border-background'
          style={{ backgroundColor: color, top: '65%' }}
        />
      )}
      <Handle
        type='source'
        position={Position.Right}
        className='!w-3 !h-3 !border-2 !border-background'
        style={{ backgroundColor: color }}
      />
    </div>
  )
}
