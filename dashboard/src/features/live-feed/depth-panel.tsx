import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { DepthData } from './types'

interface DepthPanelProps {
  depth: DepthData | undefined
}

function formatQty(qty: number): string {
  if (qty >= 1_000_000) return `${(qty / 1_000_000).toFixed(1)}M`
  if (qty >= 1_000) return `${(qty / 1_000).toFixed(1)}K`
  return qty.toLocaleString()
}

export function DepthPanel({ depth }: DepthPanelProps) {
  const [expanded, setExpanded] = useState(false)

  if (!depth || (!depth.bids?.length && !depth.asks?.length)) {
    return (
      <div className='px-4 py-3 text-sm text-muted-foreground text-center border-t'>
        No depth data
      </div>
    )
  }

  const defaultLevels = 5
  const expandedLevels = 25
  const visibleLevels = expanded ? expandedLevels : defaultLevels

  const bids = depth.bids?.slice(0, visibleLevels) ?? []
  const asks = depth.asks?.slice(0, visibleLevels) ?? []

  const maxBidQty = Math.max(...(bids.map((b) => b.qty) ?? [1]))
  const maxAskQty = Math.max(...(asks.map((a) => a.qty) ?? [1]))

  const tbq = depth.tbq ?? 0
  const tsq = depth.tsq ?? 0
  const totalQty = tbq + tsq
  const buyPct = totalQty > 0 ? (tbq / totalQty) * 100 : 50

  return (
    <div className='border-t'>
      {/* TBQ vs TSQ bar */}
      <div className='px-4 py-2'>
        <div className='flex justify-between text-xs text-muted-foreground mb-1'>
          <span>TBQ: {formatQty(tbq)}</span>
          <span>TSQ: {formatQty(tsq)}</span>
        </div>
        <div className='flex h-1.5 rounded-full overflow-hidden bg-muted'>
          <div
            className='bg-green-500 transition-all duration-300'
            style={{ width: `${buyPct}%` }}
          />
          <div
            className='bg-red-500 transition-all duration-300'
            style={{ width: `${100 - buyPct}%` }}
          />
        </div>
      </div>

      {/* Spread */}
      {depth.bestBid != null && depth.bestAsk != null && (
        <div className='px-4 pb-1 text-xs text-muted-foreground text-center'>
          Spread: {(depth.bestAsk - depth.bestBid).toFixed(2)} (
          {(((depth.bestAsk - depth.bestBid) / depth.bestAsk) * 100).toFixed(3)}%)
        </div>
      )}

      {/* Depth table */}
      <ScrollArea className={expanded ? 'max-h-80' : ''}>
        <div className='grid grid-cols-2 gap-0 text-xs'>
          {/* Header */}
          <div className='grid grid-cols-3 gap-1 px-4 py-1 text-muted-foreground font-medium border-b'>
            <span>Orders</span>
            <span className='text-right'>Qty</span>
            <span className='text-right'>Bid</span>
          </div>
          <div className='grid grid-cols-3 gap-1 px-4 py-1 text-muted-foreground font-medium border-b'>
            <span>Ask</span>
            <span className='text-right'>Qty</span>
            <span className='text-right'>Orders</span>
          </div>

          {/* Rows */}
          {Array.from({ length: Math.max(bids.length, asks.length) }).map((_, i) => {
            const bid = bids[i]
            const ask = asks[i]

            return (
              <div key={i} className='contents'>
                {/* Bid side */}
                <div className='relative grid grid-cols-3 gap-1 px-4 py-0.5'>
                  {bid && (
                    <div
                      className='absolute inset-0 bg-green-500/10 transition-all'
                      style={{ width: `${(bid.qty / maxBidQty) * 100}%`, right: 0, left: 'auto' }}
                    />
                  )}
                  <span className='relative text-muted-foreground'>{bid?.orders ?? ''}</span>
                  <span className='relative text-right'>{bid ? formatQty(bid.qty) : ''}</span>
                  <span className='relative text-right font-medium text-green-600 dark:text-green-400'>
                    {bid?.price.toFixed(2) ?? ''}
                  </span>
                </div>

                {/* Ask side */}
                <div className='relative grid grid-cols-3 gap-1 px-4 py-0.5'>
                  {ask && (
                    <div
                      className='absolute inset-0 bg-red-500/10 transition-all'
                      style={{ width: `${(ask.qty / maxAskQty) * 100}%` }}
                    />
                  )}
                  <span className='relative font-medium text-red-600 dark:text-red-400'>
                    {ask?.price.toFixed(2) ?? ''}
                  </span>
                  <span className='relative text-right'>{ask ? formatQty(ask.qty) : ''}</span>
                  <span className='relative text-right text-muted-foreground'>
                    {ask?.orders ?? ''}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </ScrollArea>

      {/* Expand/collapse toggle */}
      {(depth.bids?.length ?? 0) > defaultLevels && (
        <div className='flex justify-center py-1 border-t'>
          <Button
            variant='ghost'
            size='sm'
            className='h-6 text-xs'
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? (
              <>
                <ChevronUp className='size-3 mr-1' /> Show less
              </>
            ) : (
              <>
                <ChevronDown className='size-3 mr-1' /> Show 25 levels
              </>
            )}
          </Button>
        </div>
      )}
    </div>
  )
}
