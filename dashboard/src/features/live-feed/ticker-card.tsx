import { useState, useRef, useEffect } from 'react'
import { ArrowDown, ArrowUp, BookOpen, MoonStar, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isMarketOpen } from '@/lib/market-hours'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { DepthPanel } from './depth-panel'
import type { TickData, DepthData, SubscribedSymbol } from './types'

interface TickerCardProps {
  sym: SubscribedSymbol
  tick: TickData | undefined
  depth: DepthData | undefined
  onRemove: (symbol: string) => void
}

function formatVolume(vol: number): string {
  if (vol >= 10_000_000) return `${(vol / 10_000_000).toFixed(2)}Cr`
  if (vol >= 100_000) return `${(vol / 100_000).toFixed(2)}L`
  if (vol >= 1_000) return `${(vol / 1_000).toFixed(1)}K`
  return vol.toLocaleString()
}

function displaySymbol(fySymbol: string): string {
  // "NSE:RELIANCE-EQ" → "RELIANCE"
  return fySymbol.replace(/^NSE:/, '').replace(/-EQ$/, '')
}

export function TickerCard({ sym, tick, depth, onRemove }: TickerCardProps) {
  const [showDepth, setShowDepth] = useState(false)
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  const prevLtp = useRef<number | undefined>()

  // Flash effect on LTP change.
  useEffect(() => {
    if (tick?.ltp == null || prevLtp.current == null) {
      prevLtp.current = tick?.ltp
      return
    }
    if (tick.ltp > prevLtp.current) {
      setFlash('up')
    } else if (tick.ltp < prevLtp.current) {
      setFlash('down')
    }
    prevLtp.current = tick.ltp

    const timer = setTimeout(() => setFlash(null), 400)
    return () => clearTimeout(timer)
  }, [tick?.ltp])

  const isPositive = (tick?.change ?? 0) >= 0
  const hasTick = tick?.ltp != null

  return (
    <Card
      className={cn(
        'overflow-hidden transition-shadow py-0 card-accent-live',
        flash === 'up' && 'ring-1 ring-live/40',
        flash === 'down' && 'ring-1 ring-red-500/40'
      )}
    >
      <CardHeader className='flex flex-row items-center justify-between pb-2 pt-4 px-4 gap-2'>
        <div className='flex items-center gap-2 min-w-0'>
          <CardTitle className='text-sm font-semibold truncate'>
            {displaySymbol(sym.symbol)}
          </CardTitle>
          <Badge variant='outline' className='text-[10px] shrink-0'>
            EQ
          </Badge>
        </div>
        <div className='flex items-center gap-1 shrink-0'>
          <Button
            variant='ghost'
            size='icon'
            className='size-7'
            onClick={() => setShowDepth(!showDepth)}
            title='Toggle depth'
          >
            <BookOpen className='size-3.5' />
          </Button>
          <Button
            variant='ghost'
            size='icon'
            className='size-7'
            onClick={() => onRemove(sym.symbol)}
            title='Remove'
          >
            <X className='size-3.5' />
          </Button>
        </div>
      </CardHeader>

      <CardContent className='px-4 pb-4 pt-0'>
        {!hasTick ? (
          <div className='flex flex-col items-center py-4 text-muted-foreground text-sm'>
            <span className='text-2xl font-semibold text-foreground'>--</span>
            {isMarketOpen() ? (
              <span className='text-xs mt-1 animate-pulse'>Waiting for tick...</span>
            ) : (
              <span className='text-xs mt-1 flex items-center gap-1'>
                <MoonStar className='size-3' />
                Market Closed
              </span>
            )}
          </div>
        ) : (
          <>
            {/* LTP + Change */}
            <div className='flex items-baseline gap-3 mb-3'>
              <span
                className={cn(
                  'text-2xl font-bold tabular-nums transition-colors',
                  flash === 'up' && 'text-green-500',
                  flash === 'down' && 'text-red-500'
                )}
              >
                {tick.ltp!.toFixed(2)}
              </span>
              <div
                className={cn(
                  'flex items-center gap-0.5 text-sm font-medium',
                  isPositive ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
                )}
              >
                {isPositive ? <ArrowUp className='size-3.5' /> : <ArrowDown className='size-3.5' />}
                <span className='tabular-nums'>
                  {tick.change != null ? `${tick.change >= 0 ? '+' : ''}${tick.change.toFixed(2)}` : '--'}
                </span>
                <span className='tabular-nums'>
                  ({tick.changePct != null ? `${tick.changePct >= 0 ? '+' : ''}${tick.changePct.toFixed(2)}%` : '--'})
                </span>
              </div>
            </div>

            {/* OHLC row */}
            <div className='grid grid-cols-4 gap-2 text-xs mb-2'>
              <div>
                <span className='text-muted-foreground'>Open</span>
                <div className='font-medium tabular-nums'>{tick.open?.toFixed(2) ?? '--'}</div>
              </div>
              <div>
                <span className='text-muted-foreground'>High</span>
                <div className='font-medium tabular-nums text-green-600 dark:text-green-400'>
                  {tick.high?.toFixed(2) ?? '--'}
                </div>
              </div>
              <div>
                <span className='text-muted-foreground'>Low</span>
                <div className='font-medium tabular-nums text-red-600 dark:text-red-400'>
                  {tick.low?.toFixed(2) ?? '--'}
                </div>
              </div>
              <div>
                <span className='text-muted-foreground'>Prev Cl</span>
                <div className='font-medium tabular-nums'>{tick.prevClose?.toFixed(2) ?? '--'}</div>
              </div>
            </div>

            {/* Volume + timestamp */}
            <div className='flex items-center justify-between text-xs text-muted-foreground'>
              <span>
                Vol: <span className='font-medium text-foreground'>{tick.volume != null ? formatVolume(tick.volume) : '--'}</span>
              </span>
              <span>
                {new Date(tick.ts * 1000).toLocaleTimeString('en-IN', {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                  hour12: false,
                })}
              </span>
            </div>
          </>
        )}
      </CardContent>

      {/* Depth panel */}
      {showDepth && <DepthPanel depth={depth} />}
    </Card>
  )
}
