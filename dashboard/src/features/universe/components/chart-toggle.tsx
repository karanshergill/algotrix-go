import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { HexbinChart } from './hexbin-chart'
import { RidgelinePlot } from './ridgeline-plot'
import type { FilteredStock, FilterState, Stock } from '../types'

type ChartMode = 'hexbin' | 'ridgeline'

interface ChartToggleProps {
  stocks: FilteredStock[]
  allStocks: Stock[]
  filters: FilterState
  onStockClick?: (isin: string) => void
}

export function ChartToggle({
  stocks,
  allStocks,
  filters,
  onStockClick,
}: ChartToggleProps) {
  const [mode, setMode] = useState<ChartMode>('hexbin')

  return (
    <div className='flex h-full flex-col'>
      <div className='mb-2 flex items-center justify-between'>
        <h3 className='text-xs font-medium text-muted-foreground'>
          {mode === 'hexbin' ? 'Volume vs Turnover Density' : 'Turnover Distribution by Filter Stage'}
        </h3>
        <div className='flex gap-1'>
          <Button
            variant={mode === 'hexbin' ? 'default' : 'outline'}
            size='sm'
            className='h-6 px-2 text-[10px]'
            onClick={() => setMode('hexbin')}
          >
            Hexbin
          </Button>
          <Button
            variant={mode === 'ridgeline' ? 'default' : 'outline'}
            size='sm'
            className='h-6 px-2 text-[10px]'
            onClick={() => setMode('ridgeline')}
          >
            Ridgeline
          </Button>
        </div>
      </div>
      <div className='relative min-h-0 flex-1'>
        {mode === 'hexbin' ? (
          <HexbinChart stocks={stocks} onStockClick={onStockClick} />
        ) : (
          <RidgelinePlot allStocks={allStocks} filters={filters} />
        )}
      </div>
    </div>
  )
}
