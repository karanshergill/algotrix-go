import { useQuery } from '@tanstack/react-query'
import type { UniverseResponse } from './types'

async function fetchUniverseMetrics(): Promise<UniverseResponse> {
  const res = await fetch('/api/universe/metrics')
  if (!res.ok) throw new Error('Failed to fetch universe metrics')
  return res.json()
}

export function useUniverseData() {
  return useQuery({
    queryKey: ['universe-metrics'],
    queryFn: fetchUniverseMetrics,
    staleTime: 5 * 60 * 1000,
  })
}
