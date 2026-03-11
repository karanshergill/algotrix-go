import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { IndexTicker } from './index-ticker'
import type { IndexQuote } from './use-index-quotes'

interface IndexTickerRotatorProps {
  symbols: string[]
  quotes: IndexQuote[]
  intervalMs?: number
}

export function IndexTickerRotator({ symbols, quotes, intervalMs = 4000 }: IndexTickerRotatorProps) {
  const [index, setIndex] = useState(0)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    if (symbols.length <= 1) return

    const timer = setInterval(() => {
      // Fade out
      setVisible(false)
      setTimeout(() => {
        setIndex((i) => (i + 1) % symbols.length)
        setVisible(true)
      }, 250) // 250ms fade-out, then swap + fade-in
    }, intervalMs)

    return () => clearInterval(timer)
  }, [symbols.length, intervalMs])

  const currentSymbol = symbols[index]
  const currentData = quotes.find((q) => q.symbol === currentSymbol)

  return (
    <span
      className={cn(
        'transition-opacity duration-250',
        visible ? 'opacity-100' : 'opacity-0'
      )}
    >
      <IndexTicker symbol={currentSymbol} data={currentData} />
    </span>
  )
}
