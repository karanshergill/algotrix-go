import { Slider } from '@/components/ui/slider'
import { Button } from '@/components/ui/button'
import { RotateCcw } from 'lucide-react'
import { DEFAULT_WEIGHTS, type MetricWeights } from './types'

type Props = {
  weights: MetricWeights
  onChange: (weights: MetricWeights) => void
  defaults?: MetricWeights
}

const METRIC_LABELS: { key: keyof MetricWeights; label: string; group: string }[] = [
  { key: 'madtv', label: 'MADTV', group: 'Tradability' },
  { key: 'amihud', label: 'Amihud', group: 'Tradability' },
  { key: 'tradeSize', label: 'Trade Size', group: 'Tradability' },
  { key: 'atrPct', label: 'ATR%', group: 'Tradability' },
  { key: 'adrPct', label: 'ADR%', group: 'Opportunity' },
  { key: 'rangeEff', label: 'Range Eff', group: 'Opportunity' },
  { key: 'parkinson', label: 'Parkinson', group: 'Opportunity' },
  { key: 'momentum', label: 'Momentum', group: 'Opportunity' },
]

function normalizedPct(value: number, total: number): string {
  if (total === 0) return '0%'
  return `${((value / total) * 100).toFixed(0)}%`
}

export function WatchlistWeightSliders({ weights, onChange, defaults }: Props) {
  const total = Object.values(weights).reduce((a, b) => a + b, 0)
  const resetTarget = defaults ?? DEFAULT_WEIGHTS

  const handleChange = (key: keyof MetricWeights, value: number) => {
    onChange({ ...weights, [key]: value })
  }

  const handleReset = () => {
    onChange({ ...resetTarget })
  }

  const tradability = METRIC_LABELS.filter((m) => m.group === 'Tradability')
  const opportunity = METRIC_LABELS.filter((m) => m.group === 'Opportunity')

  const tradabilityTotal = tradability.reduce((sum, m) => sum + weights[m.key], 0)
  const opportunityTotal = opportunity.reduce((sum, m) => sum + weights[m.key], 0)

  return (
    <div className='space-y-4'>
      <div className='flex items-center justify-between'>
        <div>
          <h4 className='text-xs font-medium text-muted-foreground'>Scoring Weights</h4>
          <p className='text-[10px] text-muted-foreground/60 mt-0.5'>
            Weights auto-normalize to 100%. Changing one slider changes the effective % of all metrics.
          </p>
        </div>
        <Button
          variant='ghost'
          size='sm'
          className='h-6 text-xs gap-1'
          onClick={handleReset}
        >
          <RotateCcw size={12} />
          Reset
        </Button>
      </div>

      {/* Tradability group */}
      <div>
        <div className='text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-2'>
          Tradability ({normalizedPct(tradabilityTotal, total)})
        </div>
        <div className='space-y-2.5'>
          {tradability.map((m) => (
            <SliderRow
              key={m.key}
              label={m.label}
              value={weights[m.key]}
              normalizedPct={normalizedPct(weights[m.key], total)}
              onChange={(v) => handleChange(m.key, v)}
            />
          ))}
        </div>
      </div>

      {/* Opportunity group */}
      <div>
        <div className='text-[10px] uppercase tracking-wider text-muted-foreground/70 mb-2'>
          Opportunity ({normalizedPct(opportunityTotal, total)})
        </div>
        <div className='space-y-2.5'>
          {opportunity.map((m) => (
            <SliderRow
              key={m.key}
              label={m.label}
              value={weights[m.key]}
              normalizedPct={normalizedPct(weights[m.key], total)}
              onChange={(v) => handleChange(m.key, v)}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function SliderRow({
  label,
  value,
  normalizedPct,
  onChange,
}: {
  label: string
  value: number
  normalizedPct: string
  onChange: (value: number) => void
}) {
  return (
    <div className='flex items-center gap-3'>
      <span className='text-xs w-20 shrink-0'>{label}</span>
      <Slider
        value={[value]}
        min={0}
        max={30}
        step={1}
        onValueChange={([v]) => onChange(v)}
        className='flex-1'
      />
      <span className='text-xs tabular-nums w-6 text-right font-medium'>{value}</span>
      <span className='text-[10px] tabular-nums text-muted-foreground w-10 text-right'>→ {normalizedPct}</span>
    </div>
  )
}
