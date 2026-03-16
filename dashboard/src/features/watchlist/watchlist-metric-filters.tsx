import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { RotateCcw } from 'lucide-react'
import type { MetricFilters } from './types'

const FILTER_DEFS: {
  key: keyof MetricFilters
  label: string
  dir: 'min' | 'max'
  unit: string
  placeholder: string
}[] = [
  { key: 'minADRPct', label: 'ADR%', dir: 'min', unit: '%', placeholder: 'e.g. 3.0' },
  { key: 'minRangeEff', label: 'Range Eff', dir: 'min', unit: '', placeholder: 'e.g. 0.35' },
  { key: 'minMomentum', label: 'Momentum', dir: 'min', unit: '%', placeholder: 'e.g. -5' },
  { key: 'minParkinson', label: 'Parkinson', dir: 'min', unit: '', placeholder: 'e.g. 0.02' },
  { key: 'maxAmihud', label: 'Amihud', dir: 'max', unit: '', placeholder: 'e.g. 1e-10' },
  { key: 'minTradeSize', label: 'Trade Size', dir: 'min', unit: '₹', placeholder: 'e.g. 30000' },
  { key: 'minATRPct', label: 'ATR%', dir: 'min', unit: '%', placeholder: 'e.g. 2.0' },
]

type Props = {
  filters: MetricFilters
  onChange: (f: MetricFilters) => void
}

export function WatchlistMetricFilters({ filters, onChange }: Props) {
  const activeCount = Object.values(filters).filter((v) => v !== '').length

  const handleReset = () => {
    onChange(emptyFilters())
  }

  return (
    <div className='space-y-3'>
      <div className='flex items-center justify-between'>
        <div>
          <h4 className='text-xs font-medium text-muted-foreground'>
            Metric Filters
            {activeCount > 0 && (
              <span className='ml-1.5 text-[10px] bg-primary/15 text-primary px-1.5 py-0.5 rounded-full'>
                {activeCount} active
              </span>
            )}
          </h4>
          <p className='text-[10px] text-muted-foreground/60 mt-0.5'>
            Client-side filters on qualified stocks. Blank = no filter.
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
        {FILTER_DEFS.map(({ key, label, dir, unit, placeholder }) => (
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
          </div>
        ))}
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
    if (f.minMomentum && s.Momentum5D < Number(f.minMomentum)) return false
    if (f.minParkinson && s.Parkinson < Number(f.minParkinson)) return false
    if (f.maxAmihud && s.Amihud > Number(f.maxAmihud)) return false
    if (f.minTradeSize && s.TradeSize < Number(f.minTradeSize)) return false
    if (f.minATRPct && s.ATRPct < Number(f.minATRPct)) return false
    return true
  })
}
