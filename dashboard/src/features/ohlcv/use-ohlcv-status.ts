import { useQuery } from '@tanstack/react-query'
import type { OhlcvStatusResponse } from './types'

export function useOhlcvStatus(from: string, to: string) {
  return useQuery<OhlcvStatusResponse>({
    queryKey: ['ohlcv-status', from, to],
    queryFn: async () => {
      const search = new URLSearchParams({ from, to })
      const response = await fetch(`/api/ohlcv/status?${search.toString()}`)
      if (!response.ok) {
        throw new Error('Failed to fetch OHLCV status')
      }
      return response.json()
    },
    staleTime: 30_000,
  })
}
