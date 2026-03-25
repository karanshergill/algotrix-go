import { useEffect, useRef, useState } from 'react'

export interface LivePrice {
  ltp: number
  prev: number  // previous LTP for direction coloring
}

/**
 * Listens to /features endpoint for real-time LTP updates.
 * Returns Map<ISIN, LivePrice> with current and previous LTP.
 */
export function useLivePrices(isins: string[]): Map<string, LivePrice> {
  const [prices, setPrices] = useState<Map<string, LivePrice>>(new Map())
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (isins.length === 0) return

    const isinSet = new Set(isins)

    const fetchPrices = async () => {
      try {
        const res = await fetch('/api/live-prices/all')
        if (!res.ok) return
        const data: Record<string, { LTP: number }> = await res.json()

        setPrices(prev => {
          const next = new Map<string, LivePrice>()
          for (const [isin, stock] of Object.entries(data)) {
            if (!isinSet.has(isin)) continue
            const old = prev.get(isin)
            next.set(isin, {
              ltp: stock.LTP,
              prev: old?.ltp ?? stock.LTP,
            })
          }
          return next
        })
      } catch {}
    }

    fetchPrices()
    intervalRef.current = setInterval(fetchPrices, 2000)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [isins.join(',')])

  return prices
}
