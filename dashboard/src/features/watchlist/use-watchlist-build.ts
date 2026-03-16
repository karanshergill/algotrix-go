import { useQuery } from '@tanstack/react-query'
import type { BuildResult, BuildParams } from './types'

async function fetchBuildReport(params: BuildParams): Promise<BuildResult> {
  const sp = new URLSearchParams({
    lookback: String(params.lookback),
    fnoOnly: String(params.fnoOnly),
    madtvFloor: String(params.madtvFloor),
  })
  const res = await fetch(`/api/watchlists/build-report?${sp}`)
  if (!res.ok) throw new Error(`Build report failed: ${res.statusText}`)
  return res.json()
}

export function useWatchlistBuild(params: BuildParams, enabled: boolean) {
  return useQuery({
    queryKey: ['watchlist-build', params.lookback, params.fnoOnly, params.madtvFloor],
    queryFn: () => fetchBuildReport(params),
    enabled,
    staleTime: 10 * 60 * 1000,
  })
}
