import { useQuery } from '@tanstack/react-query'
import type { GroupChainResponse } from './types'

async function fetchGroupChain(isin: string): Promise<GroupChainResponse> {
  const response = await fetch(`/api/symbols/${encodeURIComponent(isin)}/group-chain`)
  if (!response.ok) {
    throw new Error('Failed to fetch stock group chain')
  }

  return response.json()
}

export function useGroupChain(isin: string | null) {
  return useQuery({
    queryKey: ['group-chain', isin],
    queryFn: () => fetchGroupChain(isin as string),
    enabled: Boolean(isin),
    staleTime: 5 * 60 * 1000,
  })
}
