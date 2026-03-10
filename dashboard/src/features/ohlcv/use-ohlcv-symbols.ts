import { useQuery } from '@tanstack/react-query'
import type { Resolution } from './constants'
import type { OhlcvSymbolsResponse } from './types'

interface UseOhlcvSymbolsOptions {
  date: string
  resolution: Resolution
  enabled?: boolean
}

export function useOhlcvSymbols({
  date,
  resolution,
  enabled = true,
}: UseOhlcvSymbolsOptions) {
  return useQuery<OhlcvSymbolsResponse>({
    queryKey: ['ohlcv-symbols', date, resolution],
    queryFn: async () => {
      const search = new URLSearchParams({ date, resolution })
      const response = await fetch(`/api/ohlcv/symbols?${search.toString()}`)
      if (!response.ok) {
        throw new Error('Failed to fetch OHLCV symbols')
      }
      return response.json()
    },
    enabled: enabled && date.length > 0,
    staleTime: 30_000,
  })
}
