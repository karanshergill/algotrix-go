import { MoonStar, Radio, WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { isMarketOpen, getNextOpenLabel } from '@/lib/market-hours'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { HeaderToolbar } from '@/components/layout/header-toolbar'
import { useLiveFeed } from './use-live-feed'
import { SymbolSearch } from './symbol-search'
import { TickerCard } from './ticker-card'

export function LiveFeedPage() {
  const { symbols, ticks, depths, wsStatus, feedStatus, addSymbol, removeSymbol } = useLiveFeed()
  const isFullyConnected = wsStatus === 'connected' && feedStatus === 'connected'

  return (
    <>
      <Header>
        <div className='ms-auto flex items-center space-x-4'>
          <HeaderToolbar />
        </div>
      </Header>

      <Main>
    <div className='space-y-4'>
      {/* Header */}
      <div className='flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between'>
        <div className='flex items-center gap-3'>
          <div className='icon-bg-live'>
            <Radio className='size-4 text-live' />
          </div>
          <h1 className='text-2xl font-bold tracking-tight'>Live Feed</h1>
          <div className='flex items-center gap-1.5'>
            <Radio
              className={cn(
                'size-4',
                isFullyConnected && 'text-live',
                wsStatus === 'connecting' && 'text-yellow-500 animate-pulse',
                wsStatus === 'connected' && feedStatus === 'disconnected' && 'text-orange-500',
                wsStatus === 'disconnected' && 'text-muted-foreground'
              )}
            />
            <span className='text-xs text-muted-foreground capitalize'>
              {wsStatus === 'disconnected'
                ? 'disconnected'
                : wsStatus === 'connecting'
                  ? 'connecting'
                  : feedStatus === 'connected'
                    ? 'connected'
                    : 'feed offline'}
            </span>
          </div>
        </div>
        <SymbolSearch
          onSelect={addSymbol}
          subscribedSymbols={symbols.map((s) => s.symbol)}
        />
      </div>

      {/* Feed disconnected warning */}
      {wsStatus === 'disconnected' && !isMarketOpen() && (
        <div className='flex items-center gap-2 rounded-lg border border-muted bg-muted/30 px-4 py-3 text-sm'>
          <MoonStar className='size-4 text-muted-foreground shrink-0' />
          <span className='text-muted-foreground'>
            Market Closed — Feed offline ({getNextOpenLabel()})
          </span>
        </div>
      )}
      {wsStatus === 'disconnected' && isMarketOpen() && (
        <div className='flex items-center gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3 text-sm'>
          <WifiOff className='size-4 text-yellow-500 shrink-0' />
          <span className='text-muted-foreground'>
            WebSocket disconnected. Attempting to reconnect...
          </span>
        </div>
      )}

      {/* Hub/feed not running warning */}
      {wsStatus === 'connected' && feedStatus === 'disconnected' && (
        <div className='flex items-center gap-2 rounded-lg border border-orange-500/30 bg-orange-500/5 px-4 py-3 text-sm'>
          <WifiOff className='size-4 text-orange-500 shrink-0' />
          <span className='text-muted-foreground'>
            Feed is not running. Start it from the toolbar.
          </span>
        </div>
      )}

      {/* Symbol cards grid */}
      {symbols.length === 0 ? (
        <div className='flex flex-col items-center justify-center py-16 text-muted-foreground'>
          <Radio className='size-10 mb-3 opacity-30' />
          <p className='text-sm'>No symbols added</p>
          <p className='text-xs mt-1'>
            Search and add symbols above to start streaming live ticks
          </p>
        </div>
      ) : (
        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-3'>
          {symbols.map((sym) => (
            <TickerCard
              key={sym.symbol}
              sym={sym}
              tick={ticks[sym.symbol]}
              depth={depths[sym.symbol]}
              onRemove={removeSymbol}
            />
          ))}
        </div>
      )}

      {/* Symbol count */}
      {symbols.length > 0 && (
        <p className='text-xs text-muted-foreground text-center'>
          {symbols.length}/20 symbols
        </p>
      )}
    </div>
      </Main>
    </>
  )
}
