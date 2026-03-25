import { useEffect, useRef, useState } from 'react'

/**
 * Polls live LTP for given ISINs via /api/prices every 2 seconds.
 * Returns Map<isin, ltp> updated in near real-time.
 */
export function useLivePrices(isins: string[]): Map<string, number> {
  const [prices, setPrices] = useState<Map<string, number>>(new Map())
  const isinKey = isins.slice().sort().join(',')

  useEffect(() => {
    if (!isinKey) return

    let active = true

    const poll = async () => {
      try {
        const res = await fetch(`/api/prices?isins=${isinKey}`)
        if (res.ok && active) {
          const data = await res.json() as Record<string, number>
          setPrices(new Map(Object.entries(data)))
        }
      } catch {}
    }

    poll() // immediate first fetch
    const id = setInterval(poll, 2000)

    return () => { active = false; clearInterval(id) }
  }, [isinKey])

  return prices
}
