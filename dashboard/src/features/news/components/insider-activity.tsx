import { useState } from 'react'
import { format } from 'date-fns'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useInsiderActivity, useInsiderDrilldown } from '../use-news'
import type { InsiderAggregate, InsiderTransaction } from '../types'

const inr = new Intl.NumberFormat('en-IN')

const PERIOD_OPTIONS = [7, 30, 90] as const

type Props = { symbol?: string }

export function InsiderActivity({ symbol: filterSymbol }: Props) {
  const [days, setDays] = useState<number>(7)
  const [drillSymbol, setDrillSymbol] = useState<string | null>(null)

  const activeSymbol = filterSymbol || drillSymbol
  const { data: aggData, isLoading, isError, refetch } = useInsiderActivity(days, activeSymbol || undefined)
  const drilldown = useInsiderDrilldown(days, activeSymbol || '')

  if (isLoading) {
    return (
      <div className='space-y-2'>
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className='h-10 w-full rounded-lg' />
        ))}
      </div>
    )
  }

  if (isError) {
    return (
      <div className='flex items-center justify-center h-32 text-muted-foreground text-sm gap-2'>
        Failed to load insider activity.
        <Button variant='ghost' size='sm' onClick={() => refetch()}>
          Retry
        </Button>
      </div>
    )
  }

  // Drill-down view when a symbol is active
  if (activeSymbol) {
    const transactions: InsiderTransaction[] = drilldown.data?.pages.flatMap(
      (p: { transactions: InsiderTransaction[] }) => p.transactions
    ) ?? []

    return (
      <div>
        <div className='flex items-center gap-3 mb-4'>
          <div className='flex gap-1'>
            {PERIOD_OPTIONS.map((p) => (
              <Badge
                key={p}
                variant='outline'
                className={`cursor-pointer text-xs ${days === p ? 'bg-primary/10 text-primary border-primary/30' : ''}`}
                onClick={() => setDays(p)}
              >
                {p}d
              </Badge>
            ))}
          </div>
          {!filterSymbol && (
            <Button variant='ghost' size='sm' onClick={() => setDrillSymbol(null)}>
              Back to overview
            </Button>
          )}
          <span className='text-sm font-medium'>{activeSymbol} — Insider Transactions</span>
        </div>

        {drilldown.isLoading ? (
          <div className='space-y-2'>
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className='h-10 w-full rounded-lg' />
            ))}
          </div>
        ) : transactions.length === 0 ? (
          <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
            No insider transactions for {activeSymbol}
          </div>
        ) : (
          <>
            <Card className='overflow-hidden'>
              <table className='w-full text-sm'>
                <thead>
                  <tr className='border-b border-border bg-muted/30'>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Date</th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Acquirer</th>
                    <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Mode</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Shares</th>
                    <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map((t) => (
                    <tr key={t.id} className='border-b border-border/50 hover:bg-muted/20 transition-colors'>
                      <td className='px-4 py-2.5 tabular-nums text-xs'>
                        {format(new Date(t.transaction_date), 'dd MMM yyyy')}
                      </td>
                      <td className='px-4 py-2.5 text-xs'>{t.acquirer_name}</td>
                      <td className='px-4 py-2.5'>
                        <Badge variant='outline' className='text-[10px]'>{t.acquisition_mode}</Badge>
                      </td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        {t.shares_acquired != null ? inr.format(t.shares_acquired) : '\u2014'}
                      </td>
                      <td className='px-4 py-2.5 text-right tabular-nums'>
                        {t.value != null ? `\u20B9${inr.format(t.value)}` : '\u2014'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
            {drilldown.hasNextPage && (
              <div className='flex justify-center mt-4'>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => drilldown.fetchNextPage()}
                  disabled={drilldown.isFetchingNextPage}
                >
                  {drilldown.isFetchingNextPage ? 'Loading...' : 'Load more'}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  // Aggregated view
  const topBuyers: InsiderAggregate[] = aggData?.top_buyers ?? []
  const topSellers: InsiderAggregate[] = aggData?.top_sellers ?? []

  return (
    <div>
      <div className='flex items-center gap-3 mb-4'>
        <div className='flex gap-1'>
          {PERIOD_OPTIONS.map((p) => (
            <Badge
              key={p}
              variant='outline'
              className={`cursor-pointer text-xs ${days === p ? 'bg-primary/10 text-primary border-primary/30' : ''}`}
              onClick={() => setDays(p)}
            >
              {p}d
            </Badge>
          ))}
        </div>
        <span className='text-xs text-muted-foreground'>Click a symbol to see individual transactions</span>
      </div>

      {topBuyers.length === 0 && topSellers.length === 0 ? (
        <div className='flex items-center justify-center h-32 text-muted-foreground text-sm'>
          No insider activity in the last {days} days
        </div>
      ) : (
        <div className='grid grid-cols-1 lg:grid-cols-2 gap-6'>
          {/* Top Buyers */}
          <div>
            <h3 className='text-sm font-semibold mb-3 text-emerald-500'>Top Net Buyers</h3>
            {topBuyers.length === 0 ? (
              <p className='text-xs text-muted-foreground'>No net buyers</p>
            ) : (
              <Card className='overflow-hidden'>
                <table className='w-full text-sm'>
                  <thead>
                    <tr className='border-b border-border bg-muted/30'>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>#</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Symbol</th>
                      <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Net Value</th>
                      <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Txns</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topBuyers.map((r, i) => (
                      <tr
                        key={r.symbol}
                        className='border-b border-border/50 hover:bg-muted/20 transition-colors cursor-pointer'
                        onClick={() => setDrillSymbol(r.symbol)}
                      >
                        <td className='px-4 py-2.5 text-xs text-muted-foreground'>{i + 1}</td>
                        <td className='px-4 py-2.5 font-medium'>{r.symbol}</td>
                        <td className='px-4 py-2.5 text-right tabular-nums text-emerald-500'>
                          {'\u20B9'}{inr.format(r.net_value)}
                        </td>
                        <td className='px-4 py-2.5 text-right tabular-nums'>{r.txn_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          </div>

          {/* Top Sellers */}
          <div>
            <h3 className='text-sm font-semibold mb-3 text-red-500'>Top Net Sellers</h3>
            {topSellers.length === 0 ? (
              <p className='text-xs text-muted-foreground'>No net sellers</p>
            ) : (
              <Card className='overflow-hidden'>
                <table className='w-full text-sm'>
                  <thead>
                    <tr className='border-b border-border bg-muted/30'>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>#</th>
                      <th className='text-left px-4 py-2 text-xs font-medium text-muted-foreground'>Symbol</th>
                      <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Net Value</th>
                      <th className='text-right px-4 py-2 text-xs font-medium text-muted-foreground'>Txns</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topSellers.map((r, i) => (
                      <tr
                        key={r.symbol}
                        className='border-b border-border/50 hover:bg-muted/20 transition-colors cursor-pointer'
                        onClick={() => setDrillSymbol(r.symbol)}
                      >
                        <td className='px-4 py-2.5 text-xs text-muted-foreground'>{i + 1}</td>
                        <td className='px-4 py-2.5 font-medium'>{r.symbol}</td>
                        <td className='px-4 py-2.5 text-right tabular-nums text-red-500'>
                          {'\u20B9'}{inr.format(Math.abs(r.net_value))}
                        </td>
                        <td className='px-4 py-2.5 text-right tabular-nums'>{r.txn_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
