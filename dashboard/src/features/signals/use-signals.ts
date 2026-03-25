import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Signal, SignalSummary } from './types'

function isMarketHours(): boolean {
  const now = new Date()
  const ist = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }))
  const h = ist.getHours()
  const m = ist.getMinutes()
  const mins = h * 60 + m
  return mins >= 9 * 60 + 15 && mins <= 15 * 60 + 30
}

async function fetchSignals(date: string, screener?: string, type?: string): Promise<Signal[]> {
  const params = new URLSearchParams({ date })
  if (screener) params.set('screener', screener)
  if (type) params.set('type', type)
  const res = await fetch(`/api/signals?${params}`)
  if (!res.ok) throw new Error('Failed to fetch signals')
  return res.json()
}

async function fetchSummary(date: string): Promise<SignalSummary[]> {
  const res = await fetch(`/api/signals/summary?date=${date}`)
  if (!res.ok) throw new Error('Failed to fetch signal summary')
  return res.json()
}

/** Invalidate signal queries when a real-time WS signal arrives. */
function useSignalInvalidation() {
  const qc = useQueryClient()
  useEffect(() => {
    const handler = () => {
      qc.invalidateQueries({ queryKey: ['signals'] })
      qc.invalidateQueries({ queryKey: ['signals-summary'] })
    }
    window.addEventListener('algotrix-signal-received', handler)
    return () => window.removeEventListener('algotrix-signal-received', handler)
  }, [qc])
}

export function useSignals(date: string, screener?: string, type?: string) {
  useSignalInvalidation()
  return useQuery({
    queryKey: ['signals', date, screener, type],
    queryFn: () => fetchSignals(date, screener, type),
    refetchInterval: isMarketHours() ? 10_000 : false,
  })
}

export function useSignalSummary(date: string) {
  return useQuery({
    queryKey: ['signals-summary', date],
    queryFn: () => fetchSummary(date),
    refetchInterval: isMarketHours() ? 10_000 : false,
  })
}
