import { useQuery } from '@tanstack/react-query'
import { useFeedStatus } from '@/features/feed/use-feed-status'

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

function isMarketOpen(): boolean {
  const now = new Date()
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const day = ist.getDay()
  if (day === 0 || day === 6) return false
  const t = ist.getHours() * 60 + ist.getMinutes()
  return t >= 9 * 60 + 15 && t <= 15 * 60 + 30
}

async function fetchQuotes(symbols: string[]): Promise<IndexQuote[]> {
  const res = await fetch(`/api/indices/quotes?symbols=${symbols.join(',')}`)
  if (!res.ok) throw new Error('Failed to fetch quotes')
  return res.json()
}

export function useIndexQuotes(symbols: string[]) {
  const { data: feedStatus } = useFeedStatus()
  const feedConnected = feedStatus?.status === 'connected'
  const marketOpen = isMarketOpen()

  // Polling strategy:
  //   feed connected  → 1s (reading live DB)
  //   feed off + market open → 5s (Fyers REST)
  //   market closed → false (no polling, show last known)
  const refetchInterval = feedConnected
    ? 1_000
    : marketOpen
      ? 5_000
      : false

  return useQuery<IndexQuote[]>({
    queryKey: ['index-quotes', symbols],
    queryFn: () => fetchQuotes(symbols),
    refetchInterval,
    staleTime: 800,
    placeholderData: [],
    // Don't refetch on window focus outside market hours
    refetchOnWindowFocus: marketOpen,
  })
}
