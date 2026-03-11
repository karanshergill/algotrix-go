import { cn } from '@/lib/utils'
import type { IndexQuote } from './use-index-quotes'

interface IndexTickerProps {
  symbol: string
  data?: IndexQuote
  className?: string
}

/** Derive a short display name from a Fyers symbol.
 *  "NSE:NIFTY50-INDEX" → "NIFTY 50"
 *  "NSE:BANKNIFTY-INDEX" → "BANKNIFTY"
 */
function displayName(symbol: string): string {
  const base = symbol.replace(/^NSE:/, '').replace(/-INDEX$/, '')
  // Add space before trailing digits for readability
  return base.replace(/(\D)(\d+)$/, '$1 $2')
}

export function IndexTicker({ symbol, data, className }: IndexTickerProps) {
  const name = displayName(symbol)

  if (!data) {
    return (
      <span className={cn('flex items-baseline gap-1.5 text-sm tabular-nums', className)}>
        <span className='font-semibold text-muted-foreground'>{name}</span>
        <span className='text-muted-foreground/50'>—</span>
      </span>
    )
  }

  const up = data.chp >= 0
  const sign = up ? '+' : ''

  return (
    <span className={cn('flex items-baseline gap-1.5 text-sm tabular-nums', className)}>
      <span className='font-semibold text-foreground'>{name}</span>
      <span className='font-medium text-foreground'>
        {data.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
      </span>
      <span className={cn('text-xs', up ? 'text-green-500' : 'text-red-500')}>
        {sign}{data.chp.toFixed(2)}%
      </span>
    </span>
  )
}
