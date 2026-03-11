import { useEffect, useRef, useState } from 'react'
import { IndexTicker } from './index-ticker'
import type { IndexQuote } from './use-index-quotes'

interface IndexTickerRotatorProps {
  symbols: string[]
  quotes: IndexQuote[]
  intervalMs?: number
}

export function IndexTickerRotator({ symbols, quotes, intervalMs = 4000 }: IndexTickerRotatorProps) {
  const [index, setIndex] = useState(0)
  const [tick, setTick] = useState(0) // increment to trigger re-animation on same symbol
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (symbols.length <= 1) return

    timerRef.current = setInterval(() => {
      setIndex((i) => (i + 1) % symbols.length)
      setTick((t) => t + 1)
    }, intervalMs)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [symbols.length, intervalMs])

  const currentSymbol = symbols[index]
  const currentData = quotes.find((q) => q.symbol === currentSymbol)

  return (
    // key forces a DOM remount on each rotation, triggering the CSS entrance animation
    <span
      key={tick}
      style={{
        display: 'inline-block',
        animation: 'ticker-slide-in 0.3s ease-out forwards',
      }}
    >
      <IndexTicker symbol={currentSymbol} data={currentData} />
    </span>
  )
}
