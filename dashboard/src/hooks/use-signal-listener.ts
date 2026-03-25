import { useEffect, useRef } from 'react'

/**
 * Always-on WebSocket listener that dispatches 'algotrix-signal-received'
 * for ANY signal type (BUY, ALERT, BREAKOUT). Runs regardless of alert toggle.
 * This ensures the signals table auto-refreshes even when sound is muted.
 */
export function useSignalListener() {
  const wsRef = useRef<WebSocket | null>(null)
  const seenKeys = useRef(new Set<string>())

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/feed/ws`)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type !== 'signal') return

        const signal = msg.signal as Record<string, unknown> | undefined
        if (!signal) return

        const dedupKey = signal.dedup_key as string
        if (!dedupKey || seenKeys.current.has(dedupKey)) return

        seenKeys.current.add(dedupKey)
        window.dispatchEvent(new Event('algotrix-signal-received'))
      } catch {}
    }

    ws.onerror = () => ws.close()

    // Reconnect on close
    ws.onclose = () => {
      setTimeout(() => {
        if (wsRef.current === ws) {
          const newWs = new WebSocket(`${protocol}//${window.location.host}/api/feed/ws`)
          newWs.onmessage = ws.onmessage
          newWs.onerror = ws.onerror
          newWs.onclose = ws.onclose
          wsRef.current = newWs
        }
      }, 5000)
    }

    return () => {
      const ref = wsRef.current
      wsRef.current = null
      ref?.close()
    }
  }, [])
}
