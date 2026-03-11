import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

export interface FeedStatus {
  status: 'disconnected' | 'connecting' | 'connected' | 'error'
  pid: number | null
  startedAt: string | null
  symbolCount: number
  ticksLastMinute: number
  lastError: string | null
}

async function fetchStatus(): Promise<FeedStatus> {
  const res = await fetch('/api/feed/status')
  if (!res.ok) throw new Error('Failed to fetch feed status')
  return res.json()
}

export function useFeedStatus() {
  return useQuery<FeedStatus>({
    queryKey: ['feed-status'],
    queryFn: fetchStatus,
    refetchInterval: 5_000,
  })
}

export function useFeedStart() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/feed/start', { method: 'POST' })
      const json = await res.json()
      if (!res.ok) throw new Error((json as { error?: string }).error ?? 'Start failed')
      return json
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed-status'] }),
  })
}

export function useFeedStop() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const res = await fetch('/api/feed/stop', { method: 'POST' })
      const json = await res.json()
      if (!res.ok) throw new Error((json as { error?: string }).error ?? 'Stop failed')
      return json
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['feed-status'] }),
  })
}
