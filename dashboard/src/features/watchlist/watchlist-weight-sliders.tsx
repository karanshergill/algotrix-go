import { Slider } from '@/components/ui/slider'
import { Button } from '@/components/ui/button'
import { RotateCcw } from 'lucide-react'
import { DEFAULT_WEIGHTS, type MetricWeights } from './types'

type Props = {
  weights: MetricWeights
  onChange: (weights: MetricWeights) => void
  defaults?: MetricWeights
}

const METRICS: { key: keyof MetricWeights; label: string; group: 'T' | 'O' }[] = [
  { key: 'madtv', label: 'MADTV', group: 'T' },
  { key: 'amihud', label: 'Amihud', group: 'T' },
  { key: 'tradeSize', label: 'Trade Size', group: 'T' },
  { key: 'atrPct', label: 'ATR%', group: 'T' },
  { key: 'adrPct', label: 'ADR%', group: 'O' },
  { key: 'rangeEff', label: 'Range Eff', group: 'O' },
  { key: 'parkinson', label: 'Parkinson', group: 'O' },
  { key: 'momentum', label: 'Momentum', group: 'O' },
]

function pct(value: number, total: number): string {
  if (total === 0) return '0%'
  return `${((value / total) * 100).toFixed(0)}%`
}

export function WatchlistWeightSliders({ weights, onChange, defaults }: Props) {
  const total = Object.values(weights).reduce((a, b) => a + b, 0)
  const resetTarget = defaults ?? DEFAULT_WEIGHTS

  const tradabilitySum = METRICS.filter(m => m.group === 'T').reduce((s, m) => s + weights[m.key], 0)
  const opportunitySum = METRICS.filter(m => m.group === 'O').reduce((s, m) => s + weights[m.key], 0)

  return (
    <div className='space-y-2'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-3'>
          <h4 className='text-xs font-semibold uppercase tracking-wider text-muted-foreground'>
            Scoring Weights
          </h4>
          <span className='text-[10px] text-muted-foreground/50'>
            Tradability {pct(tradabilitySum, total)} · Opportunity {pct(opportunitySum, total)}
          </span>
        </div>
        <Button
          variant='ghost'
          size='sm'
          className='h-5 text-[10px] gap-1 px-2'
          onClick={() => onChange({ ...resetTarget })}
        >
          <RotateCcw size={10} />
          Reset
        </Button>
      </div>

      {/* 4-column grid of compact sliders */}
      <div className='grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-1.5'>
        {METRICS.map((m) => (
          <div key={m.key} className='flex items-center gap-2'>
            <span className='text-[10px] text-muted-foreground w-16 shrink-0 truncate'>{m.label}</span>
            <Slider
              value={[weights[m.key]]}
              min={0}
              max={30}
              step={1}
              onValueChange={([v]) => onChange({ ...weights, [m.key]: v })}
              className='flex-1 min-w-0'
            />
            <span className='text-[10px] tabular-nums text-muted-foreground w-7 text-right shrink-0'>
              {pct(weights[m.key], total)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
