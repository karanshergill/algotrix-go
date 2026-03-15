import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { useWatchlistExplain } from './use-watchlist-explain'
import { WatchlistRadarChart } from './watchlist-radar-chart'

type Props = {
  symbol: string
  lookback: number
  open: boolean
  onOpenChange: (open: boolean) => void
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <div className='flex items-center justify-between py-1.5'>
      <span className='text-sm text-muted-foreground'>{label}</span>
      <span className='text-sm font-medium tabular-nums'>{value}</span>
    </div>
  )
}

function BreakdownRow({
  metric,
  percentile,
  weight,
  points,
}: {
  metric: string
  percentile: number
  weight: number
  points: number
}) {
  return (
    <div className='grid grid-cols-4 gap-2 py-1.5 text-sm'>
      <span className='text-muted-foreground'>{metric}</span>
      <span className='tabular-nums text-right'>{percentile.toFixed(1)}</span>
      <span className='tabular-nums text-right'>{(weight * 100).toFixed(0)}%</span>
      <span className='tabular-nums text-right font-medium'>{points.toFixed(1)}</span>
    </div>
  )
}

export function WatchlistDetailDrawer({ symbol, lookback, open, onOpenChange }: Props) {
  const { data, isLoading } = useWatchlistExplain(open ? symbol : '', lookback)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className='w-[460px] sm:max-w-[460px] overflow-y-auto'>
        <SheetHeader>
          <SheetTitle className='flex items-center gap-2'>
            {symbol}
            {data?.status && (
              <Badge
                className={
                  data.status === 'qualified'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : 'bg-red-500/15 text-red-400 border-red-500/30'
                }
              >
                {data.status === 'qualified' ? `#${data.rank} of ${data.totalQualified}` : 'Rejected'}
              </Badge>
            )}
          </SheetTitle>
        </SheetHeader>

        {isLoading && (
          <div className='space-y-3 mt-4'>
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className='h-8 bg-muted/40 rounded animate-pulse' />
            ))}
          </div>
        )}

        {data?.status === 'rejected' && (
          <div className='mt-4 text-sm text-muted-foreground'>
            This stock did not qualify for the watchlist. It may have been rejected due to
            insufficient liquidity, coverage, or missing metric data.
          </div>
        )}

        {data?.status === 'qualified' && data.percentiles && data.raw && data.composite != null && (
          <div className='space-y-5 mt-4'>
            {/* Radar chart */}
            <Card className='p-3'>
              <h4 className='text-xs font-medium mb-1 text-muted-foreground'>Metric Profile</h4>
              <WatchlistRadarChart percentiles={data.percentiles} />
            </Card>

            {/* Composite score */}
            <div className='text-center'>
              <div className='text-4xl font-bold tabular-nums'>{data.composite.toFixed(1)}</div>
              <div className='text-xs text-muted-foreground'>Composite Score / 100</div>
            </div>

            {/* Raw metrics */}
            <Card className='p-4'>
              <h4 className='text-xs font-medium mb-2 text-muted-foreground'>Raw Metrics</h4>
              <div className='divide-y divide-border/50'>
                <MetricRow label='MADTV' value={`₹${(data.raw.madtv / 1e7).toFixed(2)} Cr`} />
                <MetricRow label='Amihud' value={data.raw.amihud.toExponential(2)} />
                <MetricRow label='ATR%' value={`${data.raw.atrPct.toFixed(2)}%`} />
                <MetricRow label='Parkinson' value={`${(data.raw.parkinson * 100).toFixed(2)}%`} />
                <MetricRow label='Avg Trade Size' value={`₹${data.raw.tradeSize.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`} />
                <MetricRow label='Trading Days' value={String(data.raw.tradingDays)} />
              </div>
            </Card>

            {/* Breakdown */}
            {data.breakdown && (
              <Card className='p-4'>
                <h4 className='text-xs font-medium mb-2 text-muted-foreground'>Score Breakdown</h4>
                <div className='grid grid-cols-4 gap-2 pb-1.5 text-xs text-muted-foreground border-b border-border/50'>
                  <span>Metric</span>
                  <span className='text-right'>Pctl</span>
                  <span className='text-right'>Weight</span>
                  <span className='text-right'>Points</span>
                </div>
                {data.breakdown.map((b) => (
                  <BreakdownRow key={b.metric} {...b} />
                ))}
                <div className='border-t border-border/50 pt-1.5 flex justify-between text-sm font-medium'>
                  <span>Total</span>
                  <span className='tabular-nums'>{data.composite.toFixed(1)} / 100</span>
                </div>
              </Card>
            )}

            {/* Strengths & Weaknesses */}
            <div className='grid grid-cols-2 gap-3'>
              <Card className='p-3'>
                <h4 className='text-xs font-medium mb-2 text-emerald-400'>Strengths</h4>
                {data.strengths && data.strengths.length > 0 ? (
                  <ul className='space-y-1'>
                    {data.strengths.map((s) => (
                      <li key={s} className='text-xs text-muted-foreground'>{s}</li>
                    ))}
                  </ul>
                ) : (
                  <span className='text-xs text-muted-foreground'>None above 75th pctl</span>
                )}
              </Card>
              <Card className='p-3'>
                <h4 className='text-xs font-medium mb-2 text-red-400'>Weaknesses</h4>
                {data.weaknesses && data.weaknesses.length > 0 ? (
                  <ul className='space-y-1'>
                    {data.weaknesses.map((w) => (
                      <li key={w} className='text-xs text-muted-foreground'>{w}</li>
                    ))}
                  </ul>
                ) : (
                  <span className='text-xs text-muted-foreground'>None below 30th pctl</span>
                )}
              </Card>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
