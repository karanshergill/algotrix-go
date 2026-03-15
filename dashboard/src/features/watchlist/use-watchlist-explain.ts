import { useQuery } from '@tanstack/react-query'
import type { ExplainResult } from './types'

async function fetchExplain(symbol: string, lookback: number): Promise<ExplainResult> {
  const sp = new URLSearchParams({ symbol, lookback: String(lookback) })
  const res = await fetch(`/api/watchlists/explain?${sp}`)
  if (!res.ok) throw new Error(`Explain failed: ${res.statusText}`)
  return res.json()
}

export function useWatchlistExplain(symbol: string, lookback: number) {
  return useQuery({
    queryKey: ['watchlist-explain', symbol, lookback],
    queryFn: () => fetchExplain(symbol, lookback),
    enabled: !!symbol,
    staleTime: 10 * 60 * 1000,
  })
}
