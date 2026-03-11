import { cn } from '@/lib/utils'
import type { IndexQuote } from './use-index-quotes'

interface IndexTickerProps {
  symbol: string
  data?: IndexQuote
  /** compact=true uses a tighter name slot (for pinned tickers with short names) */
  compact?: boolean
  className?: string
}

/** Derive a short display name from a Fyers symbol.
 *  "NSE:NIFTY50-INDEX"      → "NIFTY 50"
 *  "NSE:NIFTYBANK-INDEX"    → "BANK NIFTY"
 *  "NSE:NIFTYPHARMA-INDEX"  → "PHARMA"
 *  "NSE:FINNIFTY-INDEX"     → "FIN NIFTY"
 */
const NAME_MAP: Record<string, string> = {
  'NSE:NIFTY50-INDEX':      'NIFTY 50',
  'NSE:NIFTYBANK-INDEX':    'BANK NIFTY',
  'NSE:FINNIFTY-INDEX':     'FIN NIFTY',
  'NSE:MIDCPNIFTY-INDEX':   'MIDCAP SEL',
  'NSE:NIFTYIT-INDEX':      'IT',
  'NSE:NIFTYPHARMA-INDEX':  'PHARMA',
  'NSE:NIFTYMETAL-INDEX':   'METAL',
  'NSE:NIFTYAUTO-INDEX':    'AUTO',
  'NSE:NIFTYREALTY-INDEX':  'REALTY',
  'NSE:NIFTY500-INDEX':     'NIFTY 500',
  'NSE:NIFTYNXT50-INDEX':   'NEXT 50',
  'NSE:NIFTYMIDCAP150-INDEX': 'MIDCAP 150',
  'NSE:NIFTYSMALLCAP250-INDEX': 'SMALL 250',
}

function displayName(symbol: string): string {
  return NAME_MAP[symbol] ?? symbol.replace(/^NSE:/, '').replace(/-INDEX$/, '')
}

// Each slot has a fixed width — the whole ticker block never shifts
// Name: w-[6.5rem] covers "BANK NIFTY" (longest = 9 chars at ~9px each ≈ 80px)
// LTP:  w-[5rem]   covers 5-digit values like 23,905.9
// Chp:  w-[4rem]   covers -1.47%

export function IndexTicker({ symbol, data, compact = false, className }: IndexTickerProps) {
  const name = displayName(symbol)
  // Pinned (compact) tickers use a tight name slot; rotating slot needs room for long names
  const nameW = compact ? 'w-[4.5rem]' : 'w-[6.5rem]'

  if (!data) {
    return (
      <span className={cn('inline-flex items-baseline text-sm tabular-nums whitespace-nowrap', className)}>
        <span className={cn('inline-block font-semibold text-muted-foreground', nameW)}>{name}</span>
        <span className='inline-block w-[5rem] text-right text-muted-foreground/40'>—</span>
        <span className='inline-block w-[4rem]' />
      </span>
    )
  }

  const up = data.chp >= 0
  const sign = up ? '+' : ''

  return (
    <span className={cn('inline-flex items-baseline text-sm tabular-nums whitespace-nowrap', className)}>
      <span className={cn('inline-block font-semibold text-foreground leading-none', nameW)}>{name}</span>
      <span className='inline-block w-[5rem] text-right font-medium text-foreground'>
        {data.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
      </span>
      <span className={cn('inline-block w-[4rem] text-right text-xs', up ? 'text-green-500' : 'text-red-500')}>
        {sign}{data.chp.toFixed(2)}%
      </span>
    </span>
  )
}
