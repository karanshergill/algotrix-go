import { useQueries } from '@tanstack/react-query'
import { useMemo } from 'react'
import type { OhlcvStatusResponse } from './types'

async function fetchWeekStatus(from: string, to: string): Promise<OhlcvStatusResponse> {
  const search = new URLSearchParams({ from, to })
  const response = await fetch(`/api/ohlcv/status?${search.toString()}`)
  if (!response.ok) throw new Error('Failed to fetch OHLCV status')
  return response.json()
}

// Split month range into weekly chunks (Mon-Sun windows)
function getWeekChunks(from: string, to: string): { from: string; to: string }[] {
  const chunks: { from: string; to: string }[] = []
  const start = new Date(from)
  const end = new Date(to)

  let cursor = new Date(start)
  while (cursor <= end) {
    const chunkStart = cursor.toISOString().slice(0, 10)
    // Move to end of week (Sunday) or month end, whichever comes first
    const weekEnd = new Date(cursor)
    weekEnd.setDate(weekEnd.getDate() + (6 - weekEnd.getDay()))
    const chunkEnd = weekEnd > end ? end : weekEnd
    chunks.push({ from: chunkStart, to: chunkEnd.toISOString().slice(0, 10) })
    // Next Monday
    cursor = new Date(chunkEnd)
    cursor.setDate(cursor.getDate() + 1)
  }

  return chunks
}

export function useOhlcvStatus(from: string, to: string) {
  const chunks = useMemo(() => getWeekChunks(from, to), [from, to])

  const results = useQueries({
    queries: chunks.map((chunk) => ({
      queryKey: ['ohlcv-status', chunk.from, chunk.to],
      queryFn: () => fetchWeekStatus(chunk.from, chunk.to),
      staleTime: 30_000,
    })),
  })

  // Merge all week results into a single OhlcvStatusResponse as they arrive
  const merged = useMemo<OhlcvStatusResponse | undefined>(() => {
    const loaded = results.filter((r) => r.data)
    if (loaded.length === 0) return undefined

    const merged: OhlcvStatusResponse = {
      totalSymbols: loaded[0].data!.totalSymbols,
      days: {},
    }

    for (const r of loaded) {
      if (r.data) {
        Object.assign(merged.days, r.data.days)
      }
    }

    return merged
  }, [results])

  const isLoading = results.every((r) => r.isLoading)
  const isFetching = results.some((r) => r.isFetching)

  return { data: merged, isLoading, isFetching }
}
