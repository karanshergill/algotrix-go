import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

export interface TokenStatus {
  valid: boolean
  expiresAt: string | null
  userId: string | null
}

async function fetchTokenStatus(): Promise<TokenStatus> {
  const res = await fetch('/api/auth/status')
  if (!res.ok) throw new Error('Failed to fetch token status')
  return res.json()
}

export function useToken() {
  const queryClient = useQueryClient()

  const query = useQuery<TokenStatus>({
    queryKey: ['token-status'],
    queryFn: fetchTokenStatus,
    staleTime: Infinity, // never refetch automatically — we use a timer
    retry: false,
  })

  // Set a timer to flip status when token expires
  useEffect(() => {
    if (!query.data?.valid || !query.data?.expiresAt) return

    const expiresAt = new Date(query.data.expiresAt).getTime()
    const msUntilExpiry = expiresAt - Date.now()
    if (msUntilExpiry <= 0) return

    const timer = setTimeout(() => {
      queryClient.setQueryData<TokenStatus>(['token-status'], (prev) =>
        prev ? { ...prev, valid: false } : prev
      )
    }, msUntilExpiry)

    return () => clearTimeout(timer)
  }, [query.data?.expiresAt, query.data?.valid, queryClient])

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['token-status'] })

  return { ...query, invalidate }
}
