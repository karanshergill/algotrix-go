import { useQuery } from '@tanstack/react-query'
import { useDebouncedValue } from '@tanstack/react-pacer'
import type { StockMatch } from './types'

async function fetchStockSearch(
  query: string,
  limit: number
): Promise<StockMatch[]> {
  const search = new URLSearchParams({
    q: query,
    limit: String(limit),
  })

  const response = await fetch(`/api/symbols/search?${search.toString()}`)
  if (!response.ok) {
    throw new Error('Failed to search stocks')
  }

  return response.json()
}

export function useStockSearch(query: string, limit: number = 10) {
  const trimmed = query.trim()
  const [debouncedQuery] = useDebouncedValue(trimmed, { wait: 300 })

  const queryResult = useQuery({
    queryKey: ['stock-search', debouncedQuery, limit],
    queryFn: () => fetchStockSearch(debouncedQuery, limit),
    enabled: debouncedQuery.length > 0,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
  })

  return {
    ...queryResult,
    debouncedQuery,
    matches: queryResult.data ?? [],
  }
}
