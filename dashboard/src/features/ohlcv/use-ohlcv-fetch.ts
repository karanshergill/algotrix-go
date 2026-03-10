import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import type { Resolution } from './constants'
import type { OhlcvFetchJob } from './types'

interface OhlcvFetchRequest {
  resolution: Resolution
  from: string
  to: string
}

interface OhlcvFetchStartResponse {
  jobId: string
  status: 'running'
}

export function useOhlcvFetch() {
  const queryClient = useQueryClient()

  return useMutation<OhlcvFetchStartResponse, Error, OhlcvFetchRequest>({
    mutationFn: async (payload) => {
      const response = await fetch('/api/ohlcv/fetch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      })

      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(errorText || 'Failed to start OHLCV fetch')
      }

      return response.json()
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ohlcv-status'] })
    },
  })
}

export function useOhlcvFetchJob(jobId: string | null, enabled = true) {
  return useQuery<OhlcvFetchJob>({
    queryKey: ['ohlcv-fetch-job', jobId],
    queryFn: async () => {
      if (!jobId) {
        throw new Error('Job id is required')
      }

      const response = await fetch(`/api/ohlcv/fetch/${jobId}`)
      if (!response.ok) {
        throw new Error('Failed to fetch OHLCV job status')
      }

      return response.json()
    },
    enabled: enabled && jobId !== null,
    refetchInterval: 3000,
    refetchOnWindowFocus: false,
  })
}
