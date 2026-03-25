import { useEffect, useRef, useState } from 'react'

const THROTTLE_MS = 2_000
const STALENESS_MS = 15_000
const BC_CHANNEL = 'algotrix-signal-alerts'
const LS_KEY = 'algotrix-alerts-enabled'

export function isAlertsEnabled(): boolean {
  return localStorage.getItem(LS_KEY) === 'true'
}

const ALERTS_TOGGLE_EVENT = 'algotrix-alerts-toggle'

export function setAlertsEnabled(v: boolean) {
  localStorage.setItem(LS_KEY, v ? 'true' : 'false')
  window.dispatchEvent(new CustomEvent(ALERTS_TOGGLE_EVENT, { detail: v }))
}

/**
 * Listens on the existing feed WebSocket for signal messages and plays
 * an audio alert + browser notification for BUY signals.
 *
 * Alerts come ONLY from WebSocket — not from polling.
 */
export function useSignalAlerts(enabled: boolean) {
  const seenKeys = useRef(new Set<string>())
  const lastSoundAt = useRef(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const bcRef = useRef<BroadcastChannel | null>(null)
  const tabClaimed = useRef(new Set<string>())

  useEffect(() => {
    if (!enabled) return

    // Audio element — created once, reused.
    if (!audioRef.current) {
      audioRef.current = new Audio('/alert-buy.mp3')
      audioRef.current.volume = 0.7
    }

    // BroadcastChannel for tab dedup.
    const bc = new BroadcastChannel(BC_CHANNEL)
    bcRef.current = bc

    bc.onmessage = (evt) => {
      // Another tab claimed this signal — mark as seen so we don't alert.
      if (evt.data?.type === 'claim' && evt.data.dedupKey) {
        tabClaimed.current.add(evt.data.dedupKey)
      }
    }

    const handleWsMessage = (evt: MessageEvent) => {
      let msg: Record<string, unknown>
      try {
        msg = JSON.parse(evt.data)
      } catch {
        return
      }

      if (msg.type !== 'signal') return

      const signal = msg.signal as Record<string, unknown> | undefined
      if (!signal) return

      // BUY signals only.
      const signalType = (signal.signal_type as string)?.toUpperCase()
      if (signalType !== 'BUY') return

      const dedupKey = signal.dedup_key as string
      if (!dedupKey) return

      // Dedup: already seen this key?
      if (seenKeys.current.has(dedupKey)) return

      // Tab dedup: another tab already claimed this?
      if (tabClaimed.current.has(dedupKey)) return

      // Staleness: drop signals older than 15s.
      const triggeredAt = signal.triggered_at as string
      if (triggeredAt) {
        const age = Date.now() - new Date(triggeredAt).getTime()
        if (age > STALENESS_MS) return
      }

      // Mark as seen.
      seenKeys.current.add(dedupKey)

      // Notify table to refetch immediately.
      window.dispatchEvent(new Event('algotrix-signal-received'))

      // Claim across tabs.
      bc.postMessage({ type: 'claim', dedupKey })

      // Throttle sound: max one every 2s.
      const now = Date.now()
      if (now - lastSoundAt.current >= THROTTLE_MS) {
        lastSoundAt.current = now
        const audio = audioRef.current!
        audio.currentTime = 0
        audio.play().catch(() => {})
      }

      // Browser notification when tab is backgrounded.
      if (document.hidden && Notification.permission === 'granted') {
        const symbol = signal.symbol as string
        const ltp = signal.ltp as number
        const screener = signal.screener as string
        new Notification(`🟢 BUY: ${symbol} @ ₹${ltp?.toFixed(2)}`, {
          body: `Screener: ${screener}`,
          tag: dedupKey, // OS-level dedup
        })
      }
    }

    // Attach to the existing feed WebSocket.
    // The WS is at /api/feed/ws — we listen on the global message event
    // dispatched to all WebSocket instances. We need to find the active WS.
    // Approach: listen on window for 'message' won't work for WS.
    // Instead, we register a listener on the WS via a custom event bus.
    //
    // Simpler approach: create our own lightweight WS connection just for signals.
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/api/feed/ws`)

    ws.onmessage = handleWsMessage

    ws.onerror = () => ws.close()

    return () => {
      ws.close()
      bc.close()
      bcRef.current = null
    }
  }, [enabled])
}

/** Reactive state that stays in sync with the bell toggle across components. */
export function useAlertToggleState() {
  const [enabled, setEnabled] = useState(isAlertsEnabled)

  useEffect(() => {
    const handler = (e: Event) => {
      setEnabled((e as CustomEvent).detail as boolean)
    }
    window.addEventListener(ALERTS_TOGGLE_EVENT, handler)
    return () => window.removeEventListener(ALERTS_TOGGLE_EVENT, handler)
  }, [])

  return enabled
}
