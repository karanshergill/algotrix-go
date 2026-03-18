import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { BacktestRun, BacktestRunDetail } from './types'

async function fetchBacktests(): Promise<BacktestRun[]> {
  const res = await fetch('/api/backtests')
  if (!res.ok) throw new Error('Failed to fetch backtests')
  return res.json()
}

async function fetchBacktestDetail(id: string): Promise<BacktestRunDetail> {
  const res = await fetch(`/api/backtests/${id}`)
  if (!res.ok) throw new Error('Failed to fetch backtest')
  return res.json()
}

async function runBacktest(config: { type?: string; name?: string; config?: { top_n?: number; step?: number; min_mcap?: number; max_mcap?: number } }): Promise<BacktestRun> {
  const res = await fetch('/api/backtests/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.error ?? 'Backtest failed')
  }
  return res.json()
}

async function deleteBacktest(id: number): Promise<void> {
  const res = await fetch(`/api/backtests/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete backtest')
}

export function useBacktests() {
  return useQuery({
    queryKey: ['backtests'],
    queryFn: fetchBacktests,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.some((r) => r.status === 'running')) return 5000
      return false
    },
  })
}

export function useBacktestDetail(id: string) {
  return useQuery({
    queryKey: ['backtests', id],
    queryFn: () => fetchBacktestDetail(id),
    enabled: !!id,
  })
}

export function useRunBacktest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: runBacktest,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtests'] })
    },
  })
}

export function useDeleteBacktest() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: deleteBacktest,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtests'] })
    },
  })
}
