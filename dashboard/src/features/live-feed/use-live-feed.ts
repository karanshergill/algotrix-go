import { useCallback, useEffect, useRef, useState } from 'react'
import type { TickData, DepthData, SubscribedSymbol } from './types'
import { isMarketOpen } from '@/lib/market-hours'

const STORAGE_KEY = 'live-feed-symbols'
const RECONNECT_INTERVAL = 3_000
const MAX_RECONNECT_INTERVAL = 30_000

function loadSymbols(): SubscribedSymbol[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveSymbols(symbols: SubscribedSymbol[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(symbols))
}

export function useLiveFeed() {
  const [symbols, setSymbols] = useState<SubscribedSymbol[]>(loadSymbols)
  const [ticks, setTicks] = useState<Record<string, TickData>>({})
  const [depths, setDepths] = useState<Record<string, DepthData>>({})
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'disconnected'>('disconnected')
  const [feedStatus, setFeedStatus] = useState<'connected' | 'disconnected'>('disconnected')

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const backoffRef = useRef(RECONNECT_INTERVAL)
  const symbolsRef = useRef(symbols)
  symbolsRef.current = symbols

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= WebSocket.OPEN) return

    setWsStatus('connecting')

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/feed/ws`)
    wsRef.current = ws

    ws.onopen = () => {
      setWsStatus('connected')
      backoffRef.current = RECONNECT_INTERVAL

      // Re-subscribe to all persisted symbols.
      const syms = symbolsRef.current.map((s) => s.symbol)
      if (syms.length > 0) {
        ws.send(JSON.stringify({ type: 'subscribe', symbols: syms }))
      }
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'tick') {
          setTicks((prev) => ({ ...prev, [msg.symbol]: msg as TickData }))
        } else if (msg.type === 'depth') {
          setDepths((prev) => ({ ...prev, [msg.symbol]: msg as DepthData }))
        } else if (msg.type === 'hubStatus') {
          setFeedStatus(msg.connected ? 'connected' : 'disconnected')
        }
      } catch {
        // Ignore malformed messages.
      }
    }

    ws.onclose = () => {
      setWsStatus('disconnected')
      setFeedStatus('disconnected')
      wsRef.current = null
      scheduleReconnect()
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimer.current) return
    if (!isMarketOpen()) {
      backoffRef.current = 5 * 60 * 1_000
    }
    reconnectTimer.current = setTimeout(() => {
      reconnectTimer.current = null
      connect()
      backoffRef.current = Math.min(backoffRef.current * 1.5, MAX_RECONNECT_INTERVAL)
    }, backoffRef.current)
  }, [connect])

  // Connect on mount, disconnect on unmount.
  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const addSymbol = useCallback((sym: SubscribedSymbol) => {
    setSymbols((prev) => {
      if (prev.some((s) => s.symbol === sym.symbol)) return prev
      if (prev.length >= 20) return prev // Max 20 symbols
      const next = [...prev, sym]
      saveSymbols(next)
      return next
    })

    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'subscribe', symbols: [sym.symbol] }))
    }
  }, [])

  const removeSymbol = useCallback((symbol: string) => {
    setSymbols((prev) => {
      const next = prev.filter((s) => s.symbol !== symbol)
      saveSymbols(next)
      return next
    })

    // Clean up tick/depth state.
    setTicks((prev) => {
      const next = { ...prev }
      delete next[symbol]
      return next
    })
    setDepths((prev) => {
      const next = { ...prev }
      delete next[symbol]
      return next
    })

    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'unsubscribe', symbols: [symbol] }))
    }
  }, [])

  return {
    symbols,
    ticks,
    depths,
    wsStatus,
    feedStatus,
    addSymbol,
    removeSymbol,
  }
}
