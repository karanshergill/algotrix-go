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
          beta: w.beta ?? DEFAULT_WEIGHTS.beta,
          rs: w.rs ?? DEFAULT_WEIGHTS.rs,
          gap: w.gap ?? DEFAULT_WEIGHTS.gap,
          volRatio: w.volRatio ?? DEFAULT_WEIGHTS.volRatio,
          emaSlope: w.emaSlope ?? DEFAULT_WEIGHTS.emaSlope,
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

  const engineDefaultWeights = engineDefaults ? {
    madtv: engineDefaults.weights.madtv ?? DEFAULT_WEIGHTS.madtv,
    amihud: engineDefaults.weights.amihud ?? DEFAULT_WEIGHTS.amihud,
    tradeSize: engineDefaults.weights.tradeSize ?? DEFAULT_WEIGHTS.tradeSize,
    atrPct: engineDefaults.weights.atrPct ?? DEFAULT_WEIGHTS.atrPct,
    adrPct: engineDefaults.weights.adrPct ?? DEFAULT_WEIGHTS.adrPct,
    rangeEff: engineDefaults.weights.rangeEff ?? DEFAULT_WEIGHTS.rangeEff,
    parkinson: engineDefaults.weights.parkinson ?? DEFAULT_WEIGHTS.parkinson,
    momentum: engineDefaults.weights.momentum ?? DEFAULT_WEIGHTS.momentum,
    beta: engineDefaults.weights.beta ?? DEFAULT_WEIGHTS.beta,
    rs: engineDefaults.weights.rs ?? DEFAULT_WEIGHTS.rs,
    gap: engineDefaults.weights.gap ?? DEFAULT_WEIGHTS.gap,
    volRatio: engineDefaults.weights.volRatio ?? DEFAULT_WEIGHTS.volRatio,
    emaSlope: engineDefaults.weights.emaSlope ?? DEFAULT_WEIGHTS.emaSlope,
  } : undefined

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
      <div className='flex items-center justify-between px-6 py-3 border-b border-border shrink-0'>
        <div className='flex items-center gap-3'>
          <div className='p-1.5 rounded-lg bg-primary/10'>
            <Crosshair size={16} className='text-primary' />
          </div>
          <div>
            <h1 className='text-base font-semibold leading-tight'>Watchlist Builder</h1>
            <p className='text-[10px] text-muted-foreground'>
              8-metric scoring · percentile ranked · adaptive thresholds
            </p>
          </div>
        </div>
        <HeaderToolbar />
      </div>

      {/* Action bar: Lookback + Build */}
      <div className='flex items-center justify-between px-6 py-2 border-b border-border/50 shrink-0'>
        <div className='flex items-center gap-2'>
          <Label className='text-[10px] uppercase tracking-wider text-muted-foreground/70'>Lookback</Label>
          <Select
            value={String(params.lookback)}
            onValueChange={(v) => setParams((p) => ({ ...p, lookback: Number(v) }))}
          >
            <SelectTrigger className='w-24 h-7 text-xs'>
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
        <Button onClick={handleBuild} disabled={isFetching} size='sm' className='h-7 px-5'>
          {isFetching ? (
            <>
              <RefreshCw size={12} className='mr-1.5 animate-spin' />
              Building…
            </>
          ) : (
            'Build Watchlist'
          )}
        </Button>
      </div>

      {/* Scrollable content */}
      <div className='flex-1 overflow-auto'>
        <div className='px-6 py-4 space-y-3'>

          {/* Config card — universe + weights + filters */}
          <Card className='overflow-hidden'>
            {/* Row 1: All universe controls + Build button in one compact bar */}
            {/* Universe controls */}
            <div className='flex items-center gap-5 px-4 py-2.5 bg-muted/30 flex-wrap'>
                <div className='flex items-center gap-2'>
                  <Label className='text-[10px] uppercase tracking-wider text-muted-foreground/70'>Min MADTV</Label>
                  <div className='flex items-center gap-0.5'>
                    <Button
                      variant='outline'
                      size='icon'
                      className='h-7 w-7 shrink-0'
                      disabled={params.madtvFloor <= 0}
                      onClick={() => setParams((p) => ({ ...p, madtvFloor: Math.max(0, p.madtvFloor - 5e7) }))}
                    >
                      <span className='text-xs'>−</span>
                    </Button>
                    <div className='w-14 h-7 flex items-center justify-center rounded-md border border-input bg-background text-xs tabular-nums font-medium'>
                      ₹{params.madtvFloor / 1e7}Cr
                    </div>
                    <Button
                      variant='outline'
                      size='icon'
                      className='h-7 w-7 shrink-0'
                      onClick={() => setParams((p) => ({ ...p, madtvFloor: p.madtvFloor + 5e7 }))}
                    >
                      <span className='text-xs'>+</span>
                    </Button>
                  </div>
                </div>
                <div className='flex items-center gap-1.5'>
                  <Switch
                    id='fno'
                    checked={params.fnoOnly}
                    onCheckedChange={(v) => setParams((p) => ({ ...p, fnoOnly: v }))}
                    className='scale-90'
                  />
                  <Label htmlFor='fno' className='text-xs'>FnO Only</Label>
                </div>
            </div>

            {/* Row 2: Scoring Weights — full width, compact grid */}
            <div className='px-4 py-3 border-t border-border/30'>
              <WatchlistWeightSliders
                weights={params.weights}
                onChange={(w) => setParams((p) => ({ ...p, weights: w }))}
                defaults={engineDefaultWeights}
              />
            </div>

            {/* Row 3: Metric Filters — full width */}
            <div className='px-4 py-3 border-t border-border/30'>
              <WatchlistMetricFilters
                filters={metricFilters}
                onChange={setMetricFilters}
                stats={data?.Stats}
              />
            </div>
          </Card>

          {/* Results */}
          {!submitted && !data && (
            <div className='flex items-center justify-center h-24 text-muted-foreground text-sm'>
              Configure parameters and click "Build Watchlist"
            </div>
          )}

          {isLoading && (
            <div className='space-y-3'>
              <div className='flex gap-3'>
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className='h-16 w-32 rounded-lg' />
                ))}
              </div>
              <Skeleton className='h-48 w-full rounded-lg' />
            </div>
          )}

          {data && !isLoading && (
            <>
              <WatchlistFunnel
                total={data.Total}
                rejected={data.Rejected}
                qualified={qualifiedAll.length}
                filtered={activeFilterCount > 0 ? filteredStocks.length : undefined}
              />

              <div className='grid grid-cols-1 lg:grid-cols-2 gap-3'>
                <WatchlistScoreChart stocks={filteredStocks} />
                <Card className='p-4'>
                  <h3 className='text-sm font-medium mb-2'>Summary</h3>
                  <div className='grid grid-cols-2 gap-x-6 gap-y-2 text-sm'>
                    <div>
                      <span className='text-muted-foreground text-xs'>Top Score</span>
                      <div className='font-bold tabular-nums text-emerald-400'>
                        {filteredStocks[0]?.Composite.toFixed(1) ?? '—'}
                      </div>
                    </div>
                    <div>
                      <span className='text-muted-foreground text-xs'>Median Score</span>
                      <div className='font-bold tabular-nums'>
                        {filteredStocks.length > 0
                          ? filteredStocks[Math.floor(filteredStocks.length / 2)].Composite.toFixed(1)
                          : '—'}
                      </div>
                    </div>
                    <div>
                      <span className='text-muted-foreground text-xs'>Pass Rate</span>
                      <div className='font-bold tabular-nums'>
                        {data.Total > 0
                          ? ((filteredStocks.length / data.Total) * 100).toFixed(1)
                          : '0'}%
                      </div>
                    </div>
                    <div>
                      <span className='text-muted-foreground text-xs'>Lookback</span>
                      <div className='font-bold'>{submitted?.lookback ?? params.lookback}d</div>
                    </div>
                  </div>
                </Card>
              </div>

              <Card className='overflow-hidden'>
                <div className='px-4 py-2.5 border-b border-border/50'>
                  <h3 className='text-sm font-medium'>
                    Qualified Stocks
                    <span className='ml-2 text-xs text-muted-foreground'>
                      ({filteredStocks.length}{activeFilterCount > 0 ? ` of ${qualifiedAll.length}` : ''})
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
      </div>

      <WatchlistDetailDrawer
        symbol={selectedSymbol}
        lookback={submitted?.lookback ?? params.lookback}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
      />
    </div>
  )
}
