import { useCallback } from 'react'
import { Slider } from '@/components/ui/slider'
import { Badge } from '@/components/ui/badge'
// import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { type FilterState, PRESETS } from '../types'

interface FilterBarProps {
  filters: FilterState
  onChange: (filters: FilterState) => void
}

function formatNumber(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(0)}B`
  if (n >= 10_000_000) return `${(n / 10_000_000).toFixed(0)}Cr`
  if (n >= 100_000) return `${(n / 100_000).toFixed(0)}L`
  if (n >= 1000) return `${(n / 1000).toFixed(0)}K`
  return n.toString()
}

const SERIES_OPTIONS = ['EQ', 'BE', 'BZ', 'SM']

// Log-scale helpers for sliders
function logValue(min: number, max: number, position: number): number {
  const minLog = Math.log10(Math.max(min, 1))
  const maxLog = Math.log10(max)
  return Math.round(Math.pow(10, minLog + (position / 100) * (maxLog - minLog)))
}

function logPosition(min: number, max: number, value: number): number {
  const minLog = Math.log10(Math.max(min, 1))
  const maxLog = Math.log10(max)
  const valueLog = Math.log10(Math.max(value, 1))
  return ((valueLog - minLog) / (maxLog - minLog)) * 100
}

export function FilterBar({ filters, onChange }: FilterBarProps) {
  const update = useCallback(
    (partial: Partial<FilterState>) => {
      onChange({ ...filters, ...partial })
    },
    [filters, onChange]
  )

  const toggleSeries = useCallback(
    (s: string) => {
      const current = filters.series
      if (current.includes(s)) {
        update({ series: current.filter((x) => x !== s) })
      } else {
        update({ series: [...current, s] })
      }
    },
    [filters.series, update]
  )

  return (
    <div className='flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border border-border/50 bg-card/50 px-4 py-3'>
      {/* Price range */}
      <div className='flex min-w-[200px] flex-1 items-center gap-3 [&_[data-slot=slider-range]]:bg-amber-400 [&_[data-slot=slider-thumb]]:border-amber-400'>
        <span className='w-16 shrink-0 text-xs font-medium text-amber-400'>
          Price
        </span>
        <Slider
          min={0}
          max={10000}
          step={10}
          value={[filters.priceMin, filters.priceMax]}
          onValueChange={([min, max]: number[]) =>
            update({ priceMin: min, priceMax: max })
          }
        />
        <span className='w-24 shrink-0 text-right text-xs tabular-nums text-amber-400/80'>
          {filters.priceMin}–{formatNumber(filters.priceMax)}
        </span>
      </div>

      {/* Volume (log scale) */}
      <div className='flex min-w-[200px] flex-1 items-center gap-3 [&_[data-slot=slider-range]]:bg-cyan-400 [&_[data-slot=slider-thumb]]:border-cyan-400'>
        <span className='w-16 shrink-0 text-xs font-medium text-cyan-400'>
          Volume
        </span>
        <Slider
          min={0}
          max={100}
          step={1}
          value={[logPosition(1000, 50_000_000, filters.volumeMin)]}
          onValueChange={([pos]: number[]) =>
            update({ volumeMin: logValue(1000, 50_000_000, pos) })
          }
        />
        <span className='w-16 shrink-0 text-right text-xs tabular-nums text-cyan-400/80'>
          {formatNumber(filters.volumeMin)}+
        </span>
      </div>

      {/* Turnover (log scale) */}
      <div className='flex min-w-[200px] flex-1 items-center gap-3 [&_[data-slot=slider-range]]:bg-violet-400 [&_[data-slot=slider-thumb]]:border-violet-400'>
        <span className='w-16 shrink-0 text-xs font-medium text-violet-400'>
          Turnover
        </span>
        <Slider
          min={0}
          max={100}
          step={1}
          value={[logPosition(1_000_000, 5_000_000_000, filters.turnoverMin)]}
          onValueChange={([pos]: number[]) =>
            update({ turnoverMin: logValue(1_000_000, 5_000_000_000, pos) })
          }
        />
        <span className='w-16 shrink-0 text-right text-xs tabular-nums text-violet-400/80'>
          {formatNumber(filters.turnoverMin)}+
        </span>
      </div>

      {/* Min traded days */}
      <div className='flex min-w-[160px] items-center gap-3 [&_[data-slot=slider-range]]:bg-emerald-400 [&_[data-slot=slider-thumb]]:border-emerald-400'>
        <span className='w-16 shrink-0 text-xs font-medium text-emerald-400'>
          Days
        </span>
        <Slider
          min={0}
          max={20}
          step={1}
          value={[filters.minTradedDays]}
          onValueChange={([v]: number[]) => update({ minTradedDays: v })}
        />
        <span className='w-10 shrink-0 text-right text-xs tabular-nums text-emerald-400/80'>
          {filters.minTradedDays}/20
        </span>
      </div>

      {/* Series toggles */}
      <div className='flex items-center gap-1.5'>
        {SERIES_OPTIONS.map((s) => (
          <Badge
            key={s}
            variant={filters.series.includes(s) ? 'default' : 'outline'}
            className='cursor-pointer select-none'
            onClick={() => toggleSeries(s)}
          >
            {s}
          </Badge>
        ))}
      </div>

      {/* Preset selector */}
      <Select
        onValueChange={(name) => {
          const preset = PRESETS.find((p) => p.name === name)
          if (preset) onChange(preset.filters)
        }}
      >
        <SelectTrigger className='h-8 w-[130px]'>
          <SelectValue placeholder='Preset' />
        </SelectTrigger>
        <SelectContent>
          {PRESETS.map((p) => (
            <SelectItem key={p.name} value={p.name}>
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
