import { useState, useMemo, useRef, useEffect } from 'react'
import { Globe } from 'lucide-react'
import { Skeleton } from '@/components/ui/skeleton'
import { useUniverseData } from './use-universe-data'
import { type FilterState, type FilteredStock, DEFAULT_FILTERS } from './types'
import { applyFilters, computeFilterStages } from './universe-filters'
import { FilterBar } from './components/filter-bar'
import { SankeyFlow } from './components/sankey-flow'
import { ChartToggle } from './components/chart-toggle'
import { DropOffSummary } from './components/drop-off-summary'
import { DepthStrip } from './components/depth-strip'
import { StatCards } from './components/stat-cards'
import { StockTable } from './components/stock-table'

export function UniverseExplorerPage() {
  const { data, isLoading, error } = useUniverseData()
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS)
  const [highlightIsin, setHighlightIsin] = useState<string | null>(null)

  const [debouncedFilters, setDebouncedFilters] = useState<FilterState>(DEFAULT_FILTERS)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)

  useEffect(() => {
    debounceRef.current = setTimeout(() => setDebouncedFilters(filters), 150)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [filters])

  const filtered: FilteredStock[] = useMemo(() => {
    if (!data?.stocks) return []
    return applyFilters(data.stocks, debouncedFilters)
  }, [data?.stocks, debouncedFilters])

  const stages = useMemo(() => {
    if (!data?.stocks) return []
    return computeFilterStages(data.stocks, debouncedFilters)
  }, [data?.stocks, debouncedFilters])

  if (error) {
    return (
      <div className='flex h-full items-center justify-center'>
        <p className='text-destructive'>
          Failed to load universe data: {error.message}
        </p>
      </div>
    )
  }

  return (
    <div className='flex flex-col gap-4 p-6'>
      {/* Header */}
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-3'>
          <div className='icon-bg-universe'>
            <Globe className='size-5 text-universe' />
          </div>
          <div>
            <h1 className='text-lg font-semibold'>Universe Explorer</h1>
            <p className='text-xs text-muted-foreground'>
              {data?.asOf
                ? `As of ${data.asOf} · ${data.stocks.length} stocks`
                : 'Loading...'}
            </p>
          </div>
        </div>
      </div>

      {isLoading ? (
        <div className='flex flex-col gap-4'>
          <Skeleton className='h-10 w-full' />
          <Skeleton className='h-16 w-full' />
          <div className='grid grid-cols-2 gap-4'>
            <Skeleton className='h-[300px]' />
            <Skeleton className='h-[300px]' />
          </div>
          <Skeleton className='h-[400px]' />
        </div>
      ) : (
        <>
          {/* Filter Bar */}
          <FilterBar filters={filters} onChange={setFilters} />

          {/* Stat Cards — slim, at the top */}
          <StatCards
            stocks={filtered}
            totalStocks={data?.stocks.length ?? 0}
          />

          {/* Sankey + Chart */}
          <div className='grid grid-cols-2 gap-4'>
            <div className='rounded-lg border border-border/50 bg-card/50 p-3'>
              <h3 className='mb-2 text-sm font-semibold text-foreground'>
                Filter Flow
              </h3>
              <div className='h-[340px]'>
                <SankeyFlow
                  stages={stages}
                  filtered={filtered}
                  totalStocks={data?.stocks.length ?? 0}
                />
              </div>
            </div>
            <div className='h-[380px] rounded-lg border border-border/50 bg-card/50 p-3'>
              <ChartToggle
                stocks={filtered}
                allStocks={data?.stocks ?? []}
                filters={debouncedFilters}
                onStockClick={setHighlightIsin}
              />
            </div>
          </div>

          {/* Drop-off Summary + Depth Strip side by side */}
          <div className='grid grid-cols-2 gap-4'>
            <DropOffSummary stages={stages} />
            <div className='rounded-lg border border-border/50 bg-card/50 p-3'>
              <h3 className='mb-2 text-sm font-semibold text-foreground'>
                Depth Allocation
              </h3>
              <DepthStrip stocks={filtered} />
            </div>
          </div>

          {/* Stock Table */}
          <div className='rounded-lg border border-border/50 bg-card/50 p-3'>
            <h3 className='mb-2 text-sm font-semibold text-foreground'>
              Stock Universe
            </h3>
            <StockTable stocks={filtered} highlightIsin={highlightIsin} />
          </div>
        </>
      )}
    </div>
  )
}
