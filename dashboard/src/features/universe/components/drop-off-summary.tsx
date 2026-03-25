import { useMemo } from 'react'
import type { FilterStageResult } from '../universe-filters'

interface DropOffSummaryProps {
  stages: FilterStageResult[]
}

const STAGE_COLORS: Record<string, string> = {
  Series: '#8b5cf6',
  Price: '#fbbf24',
  Volume: '#22d3ee',
  Turnover: '#a78bfa',
  'Traded Days': '#34d399',
}

const STAGE_TEXT_COLORS: Record<string, string> = {
  Series: 'text-violet-400',
  Price: 'text-amber-400',
  Volume: 'text-cyan-400',
  Turnover: 'text-violet-400',
  'Traded Days': 'text-emerald-400',
}

export function DropOffSummary({ stages }: DropOffSummaryProps) {
  const maxDrop = useMemo(
    () => Math.max(...stages.map((s) => s.failCount), 1),
    [stages]
  )

  if (stages.length === 0) return null

  return (
    <div className='rounded-lg border border-border/50 bg-card/50 p-3'>
      <h3 className='mb-2 text-xs font-semibold text-foreground'>
        Per-Filter Drop-off
      </h3>
      <div className='flex flex-col gap-1.5'>
        {stages.map((stage) => {
          const pct =
            stage.inputCount > 0
              ? ((stage.failCount / stage.inputCount) * 100).toFixed(1)
              : '0.0'
          const barWidth = (stage.failCount / maxDrop) * 100
          const color = STAGE_COLORS[stage.stageName] ?? '#6b7280'
          return (
            <div key={stage.stageName} className='flex items-center gap-2'>
              <span className={`w-20 shrink-0 text-right text-[11px] font-medium ${STAGE_TEXT_COLORS[stage.stageName] ?? 'text-muted-foreground'}`}>
                {stage.stageName}
              </span>
              <div className='relative h-5 flex-1 overflow-hidden rounded-sm bg-muted/30'>
                <div
                  className='absolute inset-y-0 left-0 rounded-sm transition-all duration-300'
                  style={{
                    width: `${barWidth}%`,
                    backgroundColor: color,
                    opacity: 0.6,
                  }}
                />
                <span className='relative z-10 flex h-full items-center pl-2 text-[10px] font-medium text-foreground'>
                  {stage.failCount > 0
                    ? `−${stage.failCount.toLocaleString()} (${pct}%)`
                    : 'none'}
                </span>
              </div>
              <span className='w-14 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground'>
                {stage.passCount.toLocaleString()} left
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
