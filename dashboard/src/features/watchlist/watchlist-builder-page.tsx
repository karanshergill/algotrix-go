import { useState, useEffect } from 'react'
import { Crosshair, RefreshCw } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { useWatchlistBuild, useWatchlistDefaults } from './use-watchlist-build'
import { WatchlistFunnel } from './watchlist-funnel'
import { WatchlistTable } from './watchlist-table'
import { WatchlistScoreChart } from './watchlist-score-chart'
import { WatchlistDetailDrawer } from './watchlist-detail-drawer'
import { WatchlistWeightSliders } from './watchlist-weight-sliders'
import { WatchlistMetricFilters, emptyFilters, applyMetricFilters } from './watchlist-metric-filters'
import { DEFAULT_WEIGHTS, type BuildParams, type MetricFilters } from './types'

export function WatchlistBuilderPage() {
  const [params, setParams] = useState<BuildParams>({ lookback: 30, fnoOnly: false, madtvFloor: 1e9, weights: { ...DEFAULT_WEIGHTS } })
  const [submitted, setSubmitted] = useState<BuildParams | null>(null)
  const [metricFilters, setMetricFilters] = useState<MetricFilters>(emptyFilters())
  const [selectedSymbol, setSelectedSymbol] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)

  const { data: engineDefaults } = useWatchlistDefaults()

  // Hydrate params from engine defaults on first load.
  const [hydrated, setHydrated] = useState(false)
  useEffect(() => {
    if (engineDefaults && !hydrated) {
      const w = engineDefaults.weights
      setParams({
        lookback: engineDefaults.lookback,
        fnoOnly: engineDefaults.fnoOnly,
        madtvFloor: engineDefaults.madtvFloor,
        weights: {
          madtv: w.madtv ?? DEFAULT_WEIGHTS.madtv,
          amihud: w.amihud ?? DEFAULT_WEIGHTS.amihud,
          tradeSize: w.tradeSize ?? DEFAULT_WEIGHTS.tradeSize,
          atrPct: w.atrPct ?? DEFAULT_WEIGHTS.atrPct,
          adrPct: w.adrPct ?? DEFAULT_WEIGHTS.adrPct,
          rangeEff: w.rangeEff ?? DEFAULT_WEIGHTS.rangeEff,
          parkinson: w.parkinson ?? DEFAULT_WEIGHTS.parkinson,
          momentum: w.momentum ?? DEFAULT_WEIGHTS.momentum,
        },
      })
      setHydrated(true)
    }
  }, [engineDefaults, hydrated])

  const { data, isLoading, isFetching } = useWatchlistBuild(
    submitted ?? params,
    submitted !== null
  )

  const symbolMap = data?.Symbols ?? {}
  const qualifiedAll = data?.Qualified ?? []
  const filteredStocks = applyMetricFilters(qualifiedAll, metricFilters)
  const activeFilterCount = Object.values(metricFilters).filter((v) => v !== '').length

  const handleBuild = () => {
    setSubmitted({ ...params })
  }

  const handleRowClick = (isin: string) => {
    const sym = symbolMap[isin] ?? isin
    setSelectedSymbol(sym)
    setDrawerOpen(true)
  }

  return (
    <div className='flex flex-col h-full'>
      {/* Header */}
      <div className='flex items-center justify-between px-6 py-4 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-2 rounded-lg bg-primary/10'>
            <Crosshair size={18} className='text-primary' />
          </div>
          <div>
            <h1 className='text-lg font-semibold'>Watchlist Builder</h1>
            <p className='text-xs text-muted-foreground'>
              8-metric scoring engine &middot; percentile ranked
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Config bar */}
      <div className='px-6 py-4 border-b border-border/50 shrink-0'>
        <div className='flex items-end gap-4 flex-wrap'>
          <div className='space-y-1'>
            <Label className='text-xs'>Lookback (days)</Label>
            <Select
              value={String(params.lookback)}
              onValueChange={(v) => setParams((p) => ({ ...p, lookback: Number(v) }))}
            >
              <SelectTrigger className='w-28 h-9'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='10'>10 days</SelectItem>
                <SelectItem value='20'>20 days</SelectItem>
                <SelectItem value='30'>30 days</SelectItem>
                <SelectItem value='60'>60 days</SelectItem>
                <SelectItem value='90'>90 days</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className='space-y-1'>
            <Label className='text-xs'>MADTV Floor (₹ Cr)</Label>
            <div className='flex items-center gap-1'>
              <Button
                variant='outline'
                size='icon'
                className='h-9 w-9 shrink-0'
                disabled={params.madtvFloor <= 0}
                onClick={() => setParams((p) => ({ ...p, madtvFloor: Math.max(0, p.madtvFloor - 5e7) }))}
              >
                <span className='text-base'>−</span>
              </Button>
              <div className='w-20 h-9 flex items-center justify-center rounded-md border border-input bg-background text-sm tabular-nums'>
                {params.madtvFloor / 1e7}
              </div>
              <Button
                variant='outline'
                size='icon'
                className='h-9 w-9 shrink-0'
                onClick={() => setParams((p) => ({ ...p, madtvFloor: p.madtvFloor + 5e7 }))}
              >
                <span className='text-base'>+</span>
              </Button>
            </div>
          </div>
          <div className='flex items-center gap-2 pb-1'>
            <Switch
              id='fno'
              checked={params.fnoOnly}
              onCheckedChange={(v) => setParams((p) => ({ ...p, fnoOnly: v }))}
            />
            <Label htmlFor='fno' className='text-xs'>FnO Only</Label>
          </div>
          <Button onClick={handleBuild} disabled={isFetching} className='h-9'>
            {isFetching ? (
              <>
                <RefreshCw size={14} className='mr-1.5 animate-spin' />
                Building...
              </>
            ) : (
              'Build Watchlist'
            )}
          </Button>
        </div>

        {/* Scoring weights + metric filters — always visible */}
        <div className='grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3'>
          <Card className='p-4'>
            <WatchlistWeightSliders
              weights={params.weights}
              onChange={(w) => setParams((p) => ({ ...p, weights: w }))}
              defaults={engineDefaults ? {
                madtv: engineDefaults.weights.madtv ?? DEFAULT_WEIGHTS.madtv,
                amihud: engineDefaults.weights.amihud ?? DEFAULT_WEIGHTS.amihud,
                tradeSize: engineDefaults.weights.tradeSize ?? DEFAULT_WEIGHTS.tradeSize,
                atrPct: engineDefaults.weights.atrPct ?? DEFAULT_WEIGHTS.atrPct,
                adrPct: engineDefaults.weights.adrPct ?? DEFAULT_WEIGHTS.adrPct,
                rangeEff: engineDefaults.weights.rangeEff ?? DEFAULT_WEIGHTS.rangeEff,
                parkinson: engineDefaults.weights.parkinson ?? DEFAULT_WEIGHTS.parkinson,
                momentum: engineDefaults.weights.momentum ?? DEFAULT_WEIGHTS.momentum,
              } : undefined}
            />
          </Card>
          <Card className='p-4'>
            <WatchlistMetricFilters
              filters={metricFilters}
              onChange={setMetricFilters}
              stats={data?.Stats}
            />
          </Card>
        </div>
      </div>

      {/* Content */}
      <div className='flex-1 overflow-auto px-6 py-5 space-y-5'>
        {!submitted && !data && (
          <div className='flex items-center justify-center h-48 text-muted-foreground text-sm'>
            Configure parameters and click "Build Watchlist" to start
          </div>
        )}

        {isLoading && (
          <div className='space-y-4'>
            <div className='flex gap-3'>
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className='h-20 w-36 rounded-lg' />
              ))}
            </div>
            <Skeleton className='h-64 w-full rounded-lg' />
          </div>
        )}

        {data && !isLoading && (
          <>
            {/* Pipeline funnel */}
            <WatchlistFunnel
              total={data.Total}
              rejected={data.Rejected}
              qualified={qualifiedAll.length}
              filtered={activeFilterCount > 0 ? filteredStocks.length : undefined}
            />

            {/* Charts row */}
            <div className='grid grid-cols-1 lg:grid-cols-2 gap-4'>
              <WatchlistScoreChart stocks={filteredStocks} />
              <Card className='p-4'>
                <h3 className='text-sm font-medium mb-1'>Summary</h3>
                <div className='grid grid-cols-2 gap-x-6 gap-y-2 text-sm mt-3'>
                  <div>
                    <span className='text-muted-foreground'>Top Score</span>
                    <div className='font-bold tabular-nums text-emerald-400'>
                      {filteredStocks[0]?.Composite.toFixed(1) ?? '—'}
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Median Score</span>
                    <div className='font-bold tabular-nums'>
                      {filteredStocks.length > 0
                        ? filteredStocks[Math.floor(filteredStocks.length / 2)].Composite.toFixed(1)
                        : '—'}
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Pass Rate</span>
                    <div className='font-bold tabular-nums'>
                      {data.Total > 0
                        ? ((filteredStocks.length / data.Total) * 100).toFixed(1)
                        : '0'}%
                    </div>
                  </div>
                  <div>
                    <span className='text-muted-foreground'>Lookback</span>
                    <div className='font-bold'>{submitted?.lookback ?? params.lookback}d</div>
                  </div>
                </div>
              </Card>
            </div>

            {/* Ranked table */}
            <Card className='overflow-hidden'>
              <div className='px-4 py-3 border-b border-border/50'>
                <h3 className='text-sm font-medium'>
                  Qualified Stocks
                  <span className='ml-2 text-xs text-muted-foreground'>
                    ({filteredStocks.length}{activeFilterCount > 0 ? ` of ${qualifiedAll.length}` : ''} stocks)
                  </span>
                </h3>
              </div>
              <WatchlistTable
                stocks={filteredStocks}
                symbolLookup={symbolMap}
                onRowClick={handleRowClick}
              />
            </Card>
          </>
        )}
      </div>

      {/* Detail drawer */}
      <WatchlistDetailDrawer
        symbol={selectedSymbol}
        lookback={submitted?.lookback ?? params.lookback}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  )
}
