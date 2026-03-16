import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { RotateCcw } from 'lucide-react'
import type { MetricFilters, MetricStat } from './types'

const FILTER_DEFS: {
  key: keyof MetricFilters
  label: string
  dir: 'min' | 'max'
  unit: string
  statKey: string
  fallbackPlaceholder: string
  format: (v: number) => string
}[] = [
  { key: 'minADRPct', label: 'ADR%', dir: 'min', unit: '%', statKey: 'adrPct', fallbackPlaceholder: 'e.g. 3.0', format: (v) => v.toFixed(2) },
  { key: 'minRangeEff', label: 'Range Eff', dir: 'min', unit: '', statKey: 'rangeEff', fallbackPlaceholder: 'e.g. 0.35', format: (v) => v.toFixed(3) },
  { key: 'minMomentum', label: '|Momentum|', dir: 'min', unit: '%', statKey: 'momentum', fallbackPlaceholder: 'e.g. 3', format: (v) => (v * 100).toFixed(1) },
  { key: 'minParkinson', label: 'Parkinson', dir: 'min', unit: '', statKey: 'parkinson', fallbackPlaceholder: 'e.g. 0.02', format: (v) => v.toFixed(4) },
  { key: 'maxAmihud', label: 'Amihud', dir: 'max', unit: '', statKey: 'amihud', fallbackPlaceholder: 'e.g. 1e-10', format: (v) => v.toExponential(1) },
  { key: 'minTradeSize', label: 'Trade Size', dir: 'min', unit: '₹', statKey: 'tradeSize', fallbackPlaceholder: 'e.g. 30000', format: (v) => Math.round(v).toLocaleString() },
  { key: 'minATRPct', label: 'ATR%', dir: 'min', unit: '%', statKey: 'atrPct', fallbackPlaceholder: 'e.g. 2.0', format: (v) => v.toFixed(2) },
]

type Props = {
  filters: MetricFilters
  onChange: (f: MetricFilters) => void
  stats?: Record<string, MetricStat>
}

export function WatchlistMetricFilters({ filters, onChange, stats }: Props) {
  const activeCount = Object.values(filters).filter((v) => v !== '').length

  const handleReset = () => {
    onChange(emptyFilters())
  }

  return (
    <div className='space-y-3'>
      <div className='flex items-center justify-between'>
        <div>
          <h4 className='text-xs font-semibold uppercase tracking-wider text-muted-foreground'>
            Metric Filters
            {activeCount > 0 && (
              <span className='ml-1.5 text-[10px] normal-case tracking-normal bg-primary/15 text-primary px-1.5 py-0.5 rounded-full'>
                {activeCount} active
              </span>
            )}
          </h4>
          <p className='text-[10px] text-muted-foreground/60 mt-0.5'>
            Narrow results by raw metric values. Grayed hints show today's 25th percentile — stocks below this are in the bottom quartile. Blank = no filter.
          </p>
        </div>
        {activeCount > 0 && (
          <Button
            variant='ghost'
            size='sm'
            className='h-6 text-xs gap-1'
            onClick={handleReset}
          >
            <RotateCcw size={12} />
            Clear
          </Button>
        )}
      </div>
      <div className='grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-4 gap-y-2'>
        {FILTER_DEFS.map(({ key, label, dir, unit, statKey, fallbackPlaceholder, format }) => {
          const stat = stats?.[statKey]
          const placeholder = stat
            ? `25th pctl: ${format(stat.p25)}`
            : fallbackPlaceholder
          return (
            <div key={key} className='space-y-0.5'>
              <Label className='text-[10px] text-muted-foreground'>
                {label} <span className='opacity-60'>({dir} {unit})</span>
              </Label>
              <Input
                type='text'
                inputMode='decimal'
                className='h-7 text-xs tabular-nums'
                placeholder={placeholder}
                value={filters[key]}
                onChange={(e) => onChange({ ...filters, [key]: e.target.value })}
              />
              {stat && (
                <div className='text-[9px] text-muted-foreground/50 tabular-nums'>
                  min {format(stat.min)} · med {format(stat.median)} · max {format(stat.max)}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function emptyFilters(): MetricFilters {
  return {
    minADRPct: '',
    minRangeEff: '',
    minMomentum: '',
    minParkinson: '',
    maxAmihud: '',
    minTradeSize: '',
    minATRPct: '',
  }
}

export function applyMetricFilters(
  stocks: import('./types').StockScore[],
  filters: MetricFilters
): import('./types').StockScore[] {
  return stocks.filter((s) => {
    const f = filters
    if (f.minADRPct && s.ADRPct < Number(f.minADRPct)) return false
    if (f.minRangeEff && s.RangeEff < Number(f.minRangeEff)) return false
    if (f.minMomentum && Math.abs(s.Momentum5D) < Number(f.minMomentum) / 100) return false
    if (f.minParkinson && s.Parkinson < Number(f.minParkinson)) return false
    if (f.maxAmihud && s.Amihud > Number(f.maxAmihud)) return false
    if (f.minTradeSize && s.TradeSize < Number(f.minTradeSize)) return false
    if (f.minATRPct && s.ATRPct < Number(f.minATRPct)) return false
    return true
  })
}
