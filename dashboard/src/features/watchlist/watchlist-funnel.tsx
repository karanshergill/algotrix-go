import { Card } from '@/components/ui/card'
import { ChevronRight } from 'lucide-react'

type Props = {
  total: number
  rejected: number
  qualified: number
  filtered?: number
}

export function WatchlistFunnel({ total, rejected, qualified, filtered }: Props) {
  const stages = [
    { label: 'Universe', count: total, color: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
    { label: 'Rejected', count: rejected, color: 'bg-red-500/15 text-red-400 border-red-500/30' },
    { label: 'Qualified', count: qualified, color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
    ...(filtered !== undefined
      ? [{ label: 'Filtered', count: filtered, color: 'bg-amber-500/15 text-amber-400 border-amber-500/30' }]
      : []),
  ]

  return (
    <div className='flex items-center gap-2'>
      {stages.map((stage, i) => (
        <div key={stage.label} className='flex items-center gap-2'>
          <Card className={`px-4 py-3 border ${stage.color} bg-transparent`}>
            <div className='text-xs text-muted-foreground'>{stage.label}</div>
            <div className='text-2xl font-bold tabular-nums'>{stage.count.toLocaleString()}</div>
          </Card>
          {i < stages.length - 1 && (
            <ChevronRight size={20} className='text-muted-foreground/50 shrink-0' />
          )}
        </div>
      ))}
    </div>
  )
}
