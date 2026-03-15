import { useRef, useState } from 'react'
import { Search, X, Loader2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { useStockSearch } from './use-stock-search'
import { useGroupChain } from './use-group-chain'
import { GroupChainCard } from './group-chain-card'
import type { StockMatch } from './types'

export function StockSearch() {
  const [query, setQuery] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [selectedIsin, setSelectedIsin] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const { matches, isLoading, debouncedQuery } = useStockSearch(query)
  const { data: chainData, isLoading: chainLoading } = useGroupChain(selectedIsin)

  function handleSelect(match: StockMatch) {
    setSelectedIsin(match.isin)
    setQuery('')
    setIsOpen(false)
    inputRef.current?.blur()
  }

  function handleClose() {
    setSelectedIsin(null)
  }

  function handleBlur(e: React.FocusEvent) {
    if (containerRef.current?.contains(e.relatedTarget as Node)) return
    setTimeout(() => setIsOpen(false), 150)
  }

  return (
    <>
      {/* Search input — inline with tabs */}
      <div ref={containerRef} className='relative w-64'>
        <Search className='absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground' />
        <Input
          ref={inputRef}
          type='text'
          placeholder='Search stock...'
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setIsOpen(true)
          }}
          onFocus={() => query.trim().length > 0 && setIsOpen(true)}
          onBlur={handleBlur}
          className='pl-9 pr-9 h-9'
        />
        {query && (
          <button
            type='button'
            onClick={() => {
              setQuery('')
              setIsOpen(false)
            }}
            className='absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground'
          >
            <X className='size-4' />
          </button>
        )}

        {/* Dropdown */}
        {isOpen && debouncedQuery.length > 0 && (
          <div className='absolute right-0 z-50 mt-1 w-80 rounded-lg border border-border bg-popover shadow-lg'>
            {isLoading ? (
              <div className='flex items-center gap-2 px-4 py-3 text-sm text-muted-foreground'>
                <Loader2 className='size-4 animate-spin' />
                Searching...
              </div>
            ) : matches.length === 0 ? (
              <div className='px-4 py-3 text-sm text-muted-foreground'>
                No stocks found for &quot;{debouncedQuery}&quot;
              </div>
            ) : (
              <ul className='max-h-60 overflow-auto py-1'>
                {matches.map((match) => (
                  <li key={match.isin}>
                    <button
                      type='button'
                      className='flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-accent/50 transition-colors'
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => handleSelect(match)}
                    >
                      <span className='font-medium text-sm min-w-[80px]'>
                        {match.symbol}
                      </span>
                      <span className='text-xs text-muted-foreground truncate'>
                        {match.name}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      {/* Group chain card — fixed panel on right side */}
      {(selectedIsin && chainLoading) && (
        <div className='fixed top-16 right-6 z-50 w-[720px] max-w-[calc(100vw-16rem)]'>
          <div className='flex items-center gap-2 text-sm text-muted-foreground rounded-lg border bg-card p-4 shadow-lg'>
            <Loader2 className='size-4 animate-spin' />
            Loading group chain...
          </div>
        </div>
      )}
      {chainData && selectedIsin && (
        <div className='fixed top-16 right-6 z-50 w-[720px] max-w-[calc(100vw-16rem)] max-h-[calc(100vh-5rem)] overflow-auto rounded-lg shadow-2xl'>
          <GroupChainCard data={chainData} onClose={handleClose} />
        </div>
      )}
    </>
  )
}
