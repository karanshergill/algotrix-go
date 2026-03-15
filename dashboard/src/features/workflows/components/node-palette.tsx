import { type DragEvent } from 'react'
import { PALETTE_ITEMS, CATEGORY_COLORS, type PaletteItem, type NodeCategory } from '../types'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'

const CATEGORY_LABELS: Record<NodeCategory, string> = {
  source: 'Source',
  filter: 'Filter',
  enrich: 'Enrich',
  score: 'Score',
  logic: 'Logic',
  output: 'Output',
}

function PaletteNode({ item }: { item: PaletteItem }) {
  const color = CATEGORY_COLORS[item.category]

  const onDragStart = (event: DragEvent) => {
    event.dataTransfer.setData('application/reactflow', JSON.stringify(item))
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div
      className='flex items-center gap-2.5 rounded-md border bg-card px-3 py-2 cursor-grab active:cursor-grabbing hover:bg-accent/50 transition-colors'
      draggable
      onDragStart={onDragStart}
    >
      <div
        className='flex h-7 w-7 shrink-0 items-center justify-center rounded text-sm'
        style={{ backgroundColor: `${color}20`, color }}
      >
        {item.icon}
      </div>
      <div className='min-w-0'>
        <div className='text-sm font-medium leading-tight truncate'>{item.label}</div>
        <div className='text-xs text-muted-foreground truncate'>{item.description}</div>
      </div>
    </div>
  )
}

export function NodePalette() {
  const grouped = Object.groupBy(PALETTE_ITEMS, (item) => item.category)

  return (
    <div className='flex h-full w-64 flex-col border-r bg-background'>
      <div className='px-4 py-3 border-b'>
        <h3 className='text-sm font-semibold'>Node Palette</h3>
        <p className='text-xs text-muted-foreground mt-0.5'>Drag nodes onto the canvas</p>
      </div>
      <ScrollArea className='flex-1'>
        <div className='p-3 space-y-4'>
          {(Object.entries(grouped) as [NodeCategory, PaletteItem[]][]).map(
            ([category, items], idx) => (
              <div key={category}>
                {idx > 0 && <Separator className='mb-4' />}
                <div className='flex items-center gap-2 mb-2'>
                  <Badge
                    variant='outline'
                    className='text-xs font-medium'
                    style={{
                      borderColor: CATEGORY_COLORS[category],
                      color: CATEGORY_COLORS[category],
                    }}
                  >
                    {CATEGORY_LABELS[category]}
                  </Badge>
                </div>
                <div className='space-y-1.5'>
                  {items.map((item) => (
                    <PaletteNode key={item.label} item={item} />
                  ))}
                </div>
              </div>
            )
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
