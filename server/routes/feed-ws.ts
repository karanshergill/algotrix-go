import { Hono } from 'hono'
import type { UpgradeWebSocket, WSContext } from 'hono/ws'
import { WebSocket } from 'ws'

const HUB_URL = 'ws://127.0.0.1:3002'
const RECONNECT_INTERVAL = 3_000
const MAX_RECONNECT_INTERVAL = 30_000

// Per-browser-client subscription set (symbol filter).
type Client = WSContext<WebSocket>
const clientSubs = new Map<Client, Set<string>>()

// Single upstream connection to Go hub.
let hubWs: WebSocket | null = null
let hubReconnectTimer: ReturnType<typeof setTimeout> | null = null
let hubBackoff = RECONNECT_INTERVAL
let hubConnected = false

// feedIntent: only reconnect to hub when the feed was intentionally started.
// Set to true on /api/feed/start, false on /api/feed/stop.
let feedIntent = false

// Auto-detect running go-feed (PM2 cron starts it at 9 AM without dashboard interaction)
async function autoDetectFeed() {
  if (feedIntent) return
  try {
    const res = await fetch('http://127.0.0.1:3003/healthz', { signal: AbortSignal.timeout(2000) })
    if (res.ok) {
      console.log('[feed-ws] auto-detected running go-feed, connecting to Hub')
      feedIntent = true
      hubBackoff = RECONNECT_INTERVAL
      connectToHub()
    }
  } catch { /* go-feed not running */ }
}
autoDetectFeed()
setInterval(autoDetectFeed, 30_000)

export function setFeedIntent(intent: boolean) {
  feedIntent = intent
  if (intent) {
    // Feed was started — kick off hub connection attempt immediately.
    hubBackoff = RECONNECT_INTERVAL
    connectToHub()
  } else {
    // Feed was stopped — cancel any pending reconnect and close hub connection.
    if (hubReconnectTimer) {
      clearTimeout(hubReconnectTimer)
      hubReconnectTimer = null
    }
    if (hubWs) {
      hubWs.removeAllListeners()
      hubWs.close()
      hubWs = null
    }
    broadcastToClients(JSON.stringify({ type: 'hubStatus', connected: false }))
  }
}

function broadcastToClients(msg: string) {
  for (const [client] of clientSubs) {
    if (client.readyState === 1) client.send(msg)
  }
}

function connectToHub() {
  if (hubWs && hubWs.readyState === WebSocket.OPEN) return

  try {
    hubWs = new WebSocket(HUB_URL)
  } catch {
    scheduleHubReconnect()
    return
  }

  hubWs.on('open', () => {
    console.log('[feed-ws] connected to Go hub')
    hubBackoff = RECONNECT_INTERVAL
    hubConnected = true
    broadcastToClients(JSON.stringify({ type: 'hubStatus', connected: true }))
  })

  hubWs.on('message', (data: Buffer) => {
    const msg = data.toString()

    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(msg)
    } catch {
      return
    }

    // Signal messages go to ALL clients (no symbol filter).
    if (parsed.type === 'signal') {
      broadcastToClients(msg)
      return
    }

    // Tick/depth messages: relay only to clients subscribed to this symbol.
    const symbol = parsed.symbol as string | undefined
    if (!symbol) return

    for (const [client, subs] of clientSubs) {
      if (client.readyState !== 1) continue // 1 = OPEN
      if (!subs.has(symbol)) continue
      client.send(msg)
    }
  })

  hubWs.on('close', () => {
    console.log('[feed-ws] hub connection closed')
    hubWs = null
    hubConnected = false
    broadcastToClients(JSON.stringify({ type: 'hubStatus', connected: false }))
    scheduleHubReconnect()
  })

  hubWs.on('error', (err: Error) => {
    console.log('[feed-ws] hub connection error:', err.message)
    hubWs?.close()
    hubWs = null
    scheduleHubReconnect()
  })
}

function scheduleHubReconnect() {
  if (!feedIntent) return // feed was intentionally stopped — don't retry
  if (hubReconnectTimer) return
  hubReconnectTimer = setTimeout(() => {
    hubReconnectTimer = null
    connectToHub()
    hubBackoff = Math.min(hubBackoff * 1.5, MAX_RECONNECT_INTERVAL)
  }, hubBackoff)
}

/**
 * Creates a Hono sub-app with a /ws WebSocket route.
 * Mounted at /api/feed in index.ts, so full path is /api/feed/ws
 */
export function createFeedWsRoute(upgradeWebSocket: UpgradeWebSocket<WebSocket>) {
  const app = new Hono()

  app.get(
    '/ws',
    upgradeWebSocket((c) => {
      let wsRef: Client | null = null

      return {
        onOpen(_evt, ws) {
          wsRef = ws
          clientSubs.set(ws, new Set())
          console.log('[feed-ws] browser client connected (%d total)', clientSubs.size)
          // Send current hub status immediately so client knows feed state.
          ws.send(JSON.stringify({ type: 'hubStatus', connected: hubConnected }))
        },

        onMessage(evt, ws) {
          try {
            const msg = JSON.parse(typeof evt.data === 'string' ? evt.data : evt.data.toString())
            const subs = clientSubs.get(ws)
            if (!subs) return

            if (msg.type === 'subscribe' && Array.isArray(msg.symbols)) {
              for (const sym of msg.symbols) {
                if (typeof sym === 'string') subs.add(sym)
              }
            } else if (msg.type === 'unsubscribe' && Array.isArray(msg.symbols)) {
              for (const sym of msg.symbols) {
                subs.delete(sym)
              }
            }
          } catch {
            // Ignore malformed messages.
          }
        },

        onClose(_evt, ws) {
          clientSubs.delete(ws)
          console.log('[feed-ws] browser client disconnected (%d remaining)', clientSubs.size)
        },

        onError(_evt, ws) {
          clientSubs.delete(ws)
        },
      }
    })
  )

  console.log('[feed-ws] WebSocket relay ready on /api/feed/ws')

  return app
}
