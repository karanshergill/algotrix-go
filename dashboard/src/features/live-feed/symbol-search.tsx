import { useState, useCallback } from 'react'
import { Search } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import type { SubscribedSymbol } from './types'

interface SymbolSearchResult {
  isin: string
  symbol: string
  fy_symbol: string | null
  name: string | null
}

interface SymbolSearchProps {
  onSelect: (sym: SubscribedSymbol) => void
  subscribedSymbols: string[]
}

export function SymbolSearch({ onSelect, subscribedSymbols }: SymbolSearchProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SymbolSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)

  const search = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setResults([])
        return
      }
      setLoading(true)
      try {
        const res = await fetch(`/api/symbols/search?q=${encodeURIComponent(q)}&limit=10`)
        if (res.ok) {
          const data: SymbolSearchResult[] = await res.json()
          setResults(data)
        }
      } catch {
        // Ignore fetch errors.
      } finally {
        setLoading(false)
      }
    },
    []
  )

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value
      setQuery(val)
      setOpen(true)

      // Debounce inline with timeout.
      const timeout = setTimeout(() => search(val), 250)
      return () => clearTimeout(timeout)
    },
    [search]
  )

  const handleSelect = useCallback(
    (result: SymbolSearchResult) => {
      if (!result.fy_symbol) return
      onSelect({
        symbol: result.fy_symbol,
        isin: result.isin,
        name: result.name,
      })
      setQuery('')
      setResults([])
      setOpen(false)
    },
    [onSelect]
  )

  return (
    <div className='relative w-full max-w-md'>
      <div className='relative'>
        <Search className='absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground' />
        <Input
          placeholder='Search symbols... (e.g. RELIANCE, TCS)'
          value={query}
          onChange={handleChange}
          onFocus={() => query && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 200)}
          className='pl-9'
        />
      </div>

      {open && results.length > 0 && (
        <div className='absolute z-50 mt-1 w-full rounded-md border bg-popover shadow-lg'>
          <ul className='max-h-60 overflow-auto p-1'>
            {results.map((r) => {
              const alreadySubscribed = subscribedSymbols.includes(r.fy_symbol ?? '')
              return (
                <li key={r.isin}>
                  <button
                    type='button'
                    disabled={alreadySubscribed || !r.fy_symbol}
                    onClick={() => handleSelect(r)}
                    className='flex w-full items-center justify-between rounded-sm px-3 py-2 text-sm hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed'
                  >
                    <div className='flex flex-col items-start gap-0.5'>
                      <div className='flex items-center gap-2'>
                        <span className='font-medium'>{r.symbol}</span>
                        {r.name && (
                          <span className='text-muted-foreground text-xs truncate max-w-48'>
                            {r.name}
                          </span>
                        )}
                      </div>
                      <span className='text-xs text-muted-foreground'>{r.isin}</span>
                    </div>
                    {alreadySubscribed && (
                      <Badge variant='secondary' className='text-xs'>
                        Added
                      </Badge>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {open && query && !loading && results.length === 0 && (
        <div className='absolute z-50 mt-1 w-full rounded-md border bg-popover p-3 text-sm text-muted-foreground shadow-lg'>
          No symbols found
        </div>
      )}
    </div>
  )
}
