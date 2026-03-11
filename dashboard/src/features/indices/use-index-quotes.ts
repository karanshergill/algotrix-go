import { useQuery } from '@tanstack/react-query'

export interface IndexQuote {
  symbol: string
  ltp: number
  ch: number
  chp: number
  open: number
  high: number
  low: number
  prevClose: number
}

async function fetchQuotes(symbols: string[]): Promise<IndexQuote[]> {
  const res = await fetch(`/api/indices/quotes?symbols=${symbols.join(',')}`)
  if (!res.ok) throw new Error('Failed to fetch quotes')
  return res.json()
}

export function useIndexQuotes(symbols: string[]) {
  return useQuery<IndexQuote[]>({
    queryKey: ['index-quotes', symbols],
    queryFn: () => fetchQuotes(symbols),
    refetchInterval: 5_000,
    staleTime: 4_000,
    // Return empty array on error — don't break the header
    placeholderData: [],
  })
}
