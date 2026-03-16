import { useQuery } from '@tanstack/react-query'
import type { BuildResult, BuildParams, MetricWeights } from './types'

export type EngineDefaults = {
  lookback: number
  madtvFloor: number
  fnoOnly: boolean
  weights: Record<string, number>
}

async function fetchDefaults(): Promise<EngineDefaults> {
  const res = await fetch('/api/watchlists/defaults')
  if (!res.ok) throw new Error('Failed to fetch defaults')
  return res.json()
}

export function useWatchlistDefaults() {
  return useQuery({
    queryKey: ['watchlist-defaults'],
    queryFn: fetchDefaults,
    staleTime: Infinity, // defaults don't change at runtime
  })
}

async function fetchBuildReport(params: BuildParams): Promise<BuildResult> {
  const sp = new URLSearchParams({
    lookback: String(params.lookback),
    fnoOnly: String(params.fnoOnly),
    madtvFloor: String(params.madtvFloor),
    weights: JSON.stringify(params.weights),
  })
  const res = await fetch(`/api/watchlists/build-report?${sp}`)
  if (!res.ok) throw new Error(`Build report failed: ${res.statusText}`)
  return res.json()
}

export function useWatchlistBuild(params: BuildParams, enabled: boolean) {
  return useQuery({
    queryKey: ['watchlist-build', params.lookback, params.fnoOnly, params.madtvFloor, params.weights],
    queryFn: () => fetchBuildReport(params),
    enabled,
    staleTime: 10 * 60 * 1000,
  })
}
