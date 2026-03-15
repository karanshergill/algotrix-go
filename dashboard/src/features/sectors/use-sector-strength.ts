import { useQuery } from '@tanstack/react-query'
import type { SectorLevel, SectorStrengthResponse } from './types'

async function fetchSectorStrength(level: SectorLevel): Promise<SectorStrengthResponse> {
  const res = await fetch(`/api/sectors/strength?level=${level}`)
  if (!res.ok) throw new Error(`Failed to fetch sector strength: ${res.statusText}`)
  return res.json()
}

export function useSectorStrength(level: SectorLevel) {
  return useQuery({
    queryKey: ['sector-strength', level],
    queryFn: () => fetchSectorStrength(level),
    staleTime: 5 * 60 * 1000, // 5 min
  })
}
